"""Anker Solix Energy Manager.

Coordinates zero-export grid control across a pair of Anker Solix Solarbank
Max AC units by composing three pieces that are each independently simple:

  adapter.BatteryAdapter        <- reads/writes the official ha-anker-solix-official
                                    integration's entities, hides the mode-revert quirk.
  pd_controller.PDController    <- battery-agnostic incremental PD control loop.
  power_distribution.PowerDistribution <- splits the PD's aggregate command
                                    across the two units.

See const.py's module docstring and the README for why this integration
deliberately does not speak Modbus/cloud to the batteries itself.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import Event, HomeAssistant, EventStateChangedData
from homeassistant.helpers.event import async_track_state_change_event, async_track_time_interval

from .adapter import BatteryAdapter
from .const import (
    CONF_BATTERIES,
    CONF_GRID_POWER_SENSOR,
    CONF_MAX_CONTRACTED_POWER,
    CONF_MIN_CYCLE_INTERVAL,
    CONF_PD_DEADBAND,
    CONF_PD_DIRECTION_HYSTERESIS,
    CONF_PD_KD,
    CONF_PD_KP,
    CONF_PD_MAX_POWER_CHANGE,
    CONF_PD_MIN_CHARGE_POWER,
    CONF_PD_MIN_DISCHARGE_POWER,
    CONF_PD_TUNING_PROFILE,
    DEFAULT_MAX_CONTRACTED_POWER,
    DEFAULT_MIN_CYCLE_INTERVAL,
    DEFAULT_PD_DEADBAND,
    DEFAULT_PD_DIRECTION_HYSTERESIS,
    DEFAULT_PD_KD,
    DEFAULT_PD_KP,
    DEFAULT_PD_MAX_POWER_CHANGE,
    DEFAULT_PD_MIN_CHARGE_POWER,
    DEFAULT_PD_MIN_DISCHARGE_POWER,
    DEFAULT_PD_TUNING_PROFILE,
    DOMAIN,
    PD_TUNING_PROFILES,
    PLATFORMS,
)
from .pd_controller import PDController
from .power_distribution import PowerDistribution

_LOGGER = logging.getLogger(__name__)


def _pd_kwargs_from_entry(entry: ConfigEntry) -> dict:
    """PD gains: profile preset (options) overridden by any explicit custom values."""
    opts = entry.options
    profile = opts.get(CONF_PD_TUNING_PROFILE, DEFAULT_PD_TUNING_PROFILE)
    preset = PD_TUNING_PROFILES.get(profile, {})
    return {
        "kp": opts.get(CONF_PD_KP, preset.get(CONF_PD_KP, DEFAULT_PD_KP)),
        "kd": opts.get(CONF_PD_KD, preset.get(CONF_PD_KD, DEFAULT_PD_KD)),
        "deadband_w": opts.get(CONF_PD_DEADBAND, DEFAULT_PD_DEADBAND),
        "max_power_change_w": opts.get(CONF_PD_MAX_POWER_CHANGE, preset.get(CONF_PD_MAX_POWER_CHANGE, DEFAULT_PD_MAX_POWER_CHANGE)),
        "direction_hysteresis_w": opts.get(CONF_PD_DIRECTION_HYSTERESIS, DEFAULT_PD_DIRECTION_HYSTERESIS),
        "min_charge_power_w": opts.get(CONF_PD_MIN_CHARGE_POWER, DEFAULT_PD_MIN_CHARGE_POWER),
        "min_discharge_power_w": opts.get(CONF_PD_MIN_DISCHARGE_POWER, DEFAULT_PD_MIN_DISCHARGE_POWER),
    }


@dataclass
class EnergyManagerController:
    hass: HomeAssistant
    entry: ConfigEntry
    batteries: list[BatteryAdapter]
    pd: PDController
    power_distribution: PowerDistribution
    manual_mode_enabled: bool = False
    last_grid_power_w: float | None = None
    last_command_w: float = 0.0
    last_allocation_w: dict[str, float] = field(default_factory=dict)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _last_grid_updated: float | None = None
    _last_cycle_monotonic: float | None = None

    @property
    def grid_power_sensor(self) -> str:
        return self.entry.data[CONF_GRID_POWER_SENSOR]

    @property
    def target_w(self) -> float:
        # Zero-export/import target. A configurable non-zero target (hourly
        # balance, capacity protection, ...) is Phase 2 — see README roadmap.
        return 0.0

    async def async_update(self, *, is_periodic: bool) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            await self._run_cycle(is_periodic=is_periodic)

    async def _run_cycle(self, *, is_periodic: bool) -> None:
        for battery in self.batteries:
            battery.refresh()

        if self.manual_mode_enabled:
            return

        state = self.hass.states.get(self.grid_power_sensor)
        if state is None or state.state in ("unknown", "unavailable"):
            _LOGGER.warning("Grid power sensor %s unavailable", self.grid_power_sensor)
            return
        try:
            raw_w = float(state.state)
        except (TypeError, ValueError):
            _LOGGER.warning("Could not parse grid power sensor state: %s", state.state)
            return

        updated_ts = state.last_updated.timestamp()
        if is_periodic and self._last_grid_updated == updated_ts:
            # Timer tick with no new sensor data since the last cycle: batteries
            # are already running the last-written command, nothing to do.
            # (Simplification vs. the reference's tiered stale-cycle/backoff
            # handling — acceptable since a state-change listener also drives
            # this loop directly whenever the sensor actually updates.)
            return

        now = time.monotonic()
        elapsed_s = (now - self._last_cycle_monotonic) if self._last_cycle_monotonic is not None else None
        self._last_cycle_monotonic = now
        self._last_grid_updated = updated_ts
        self.last_grid_power_w = raw_w

        filtered_w = self.pd.filter_grid_sample(raw_w, elapsed_s)
        measured_w = sum(b.data.get("measured_power", 0.0) for b in self.batteries)

        result = self.pd.compute(
            grid_power_w=filtered_w,
            target_w=self.target_w,
            elapsed_s=elapsed_s,
            measured_battery_power_w=measured_w,
        )

        if result.within_deadband:
            # Still guard the mode-revert quirk even when idling — it isn't
            # tied to whether we're actively commanding power.
            for battery in self.batteries:
                await battery.async_ensure_third_party_control()
            return

        power_w = result.power_w
        is_charging = power_w > 0
        available = self.power_distribution.available_batteries(is_charging)

        system_capacity = sum(
            (b.max_charge_w if is_charging else b.max_discharge_w) for b in available
        )
        magnitude = min(abs(power_w), system_capacity)
        if not is_charging:
            magnitude = min(magnitude, self.entry.data.get(CONF_MAX_CONTRACTED_POWER, DEFAULT_MAX_CONTRACTED_POWER))

        selected = self.power_distribution.select_batteries(magnitude, available, is_charging)
        allocation = self.power_distribution.distribute_power(magnitude, selected, is_charging)

        self.last_allocation_w = {}
        for battery in self.batteries:
            power = allocation.get(battery, 0)
            if battery in selected and is_charging:
                await battery.async_set_power(power, 0)
                self.last_allocation_w[battery.name] = power
            elif battery in selected:
                await battery.async_set_power(0, power)
                self.last_allocation_w[battery.name] = -power
            else:
                await battery.async_set_power(0, 0)
                self.last_allocation_w[battery.name] = 0

        self.last_command_w = power_w


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    batteries = [BatteryAdapter(hass=hass, config=b) for b in entry.data[CONF_BATTERIES]]
    for b in batteries:
        b.refresh()

    controller = EnergyManagerController(
        hass=hass,
        entry=entry,
        batteries=batteries,
        pd=PDController(**_pd_kwargs_from_entry(entry)),
        power_distribution=PowerDistribution(batteries=batteries),
    )

    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = controller

    async def _on_grid_sensor_change(_event: Event[EventStateChangedData]) -> None:
        await controller.async_update(is_periodic=False)

    async def _on_timer(_now) -> None:
        await controller.async_update(is_periodic=True)

    unsub_state = async_track_state_change_event(
        hass, [controller.grid_power_sensor], _on_grid_sensor_change
    )
    min_interval = entry.data.get(CONF_MIN_CYCLE_INTERVAL, DEFAULT_MIN_CYCLE_INTERVAL)
    unsub_timer = async_track_time_interval(
        hass, _on_timer, timedelta(seconds=max(min_interval, 1.0))
    )
    entry.async_on_unload(unsub_state)
    entry.async_on_unload(unsub_timer)
    entry.async_on_unload(entry.add_update_listener(_async_options_updated))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_options_updated(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Hot-reload PD gains when a number/select entity updates entry.options."""
    controller: EnergyManagerController = hass.data[DOMAIN][entry.entry_id]
    for key, value in _pd_kwargs_from_entry(entry).items():
        setattr(controller.pd, key, value)


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
