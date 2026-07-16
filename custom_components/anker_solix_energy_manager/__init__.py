"""Anker Solix Energy Manager.

Coordinates zero-export grid control across a pair of Anker Solix Solarbank
Max AC units. Each control cycle runs, in order:

  1. Refresh battery state, accumulate consumption/price samples (always,
     even in manual mode — these are observational, not control).
  2. Manual mode check — if on, stop here entirely.
  3. Predictive grid charging (if enabled and its coverage/mode conditions
     are met) — bypasses the PD loop entirely for this cycle.
  4. Normal PD zero-export control, with EV load exclusion applied to the
     signal it sees and EV-triggered discharge blocking applied to its output.
  5. Capacity protection — a hard backstop applied to whatever step 3 or 4
     decided, so the contracted power limit is never exceeded regardless of
     which path produced the command.
  6. Battery selection/allocation (power_distribution) and the actual writes.

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
from .capacity_protection import apply_capacity_protection
from .consumption_tracker import ConsumptionTracker
from .ev_exclusion import EVLoadExclusion
from .const import (
    CONF_BATTERIES,
    CONF_EV_CHARGER_POWER_SENSORS,
    CONF_EV_DISCHARGE_BLOCK_THRESHOLD_W,
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
    CONF_PREDICTIVE_CHARGE_POWER_W,
    CONF_PREDICTIVE_CHARGING_ENABLED,
    CONF_PREDICTIVE_CHARGING_MODE,
    CONF_PREDICTIVE_COVERAGE_HOURS,
    CONF_PREDICTIVE_FIXED_SLOTS,
    CONF_PREDICTIVE_TARGET_SOC,
    CONF_PRICE_CHEAP_PERCENTILE,
    CONF_PRICE_FORECAST_ATTRIBUTE,
    CONF_PRICE_SENSOR,
    CONF_SOLAR_FORECAST_REMAINING_SENSOR,
    CONF_SOLAR_POWER_SENSOR,
    DEFAULT_EV_DISCHARGE_BLOCK_THRESHOLD_W,
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
    DEFAULT_PRICE_CHEAP_PERCENTILE,
    DOMAIN,
    PD_TUNING_PROFILES,
    PLATFORMS,
)
from .pd_controller import PDController
from .power_distribution import PowerDistribution
from .predictive_charging import PredictiveChargingManager
from .pricing import PriceTracker

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
    consumption_tracker: ConsumptionTracker
    ev_exclusion: EVLoadExclusion | None = None
    price_tracker: PriceTracker | None = None
    predictive_charging: PredictiveChargingManager | None = None

    manual_mode_enabled: bool = False
    last_grid_power_w: float | None = None
    last_command_w: float = 0.0
    last_allocation_w: dict[str, float] = field(default_factory=dict)
    predictive_charging_active: bool = False
    predictive_charging_reason: str = "disabled"

    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _last_grid_updated: float | None = None
    _last_cycle_monotonic: float | None = None

    @property
    def grid_power_sensor(self) -> str:
        return self.entry.data[CONF_GRID_POWER_SENSOR]

    @property
    def max_contracted_power_w(self) -> float:
        return self.entry.data.get(CONF_MAX_CONTRACTED_POWER, DEFAULT_MAX_CONTRACTED_POWER)

    async def async_update(self, *, is_periodic: bool) -> None:
        if self._lock.locked():
            return
        async with self._lock:
            try:
                await self._run_cycle(is_periodic=is_periodic)
            except Exception:
                # A single bad cycle (a transient entity/service error, a
                # bug in an optional Phase 2 module, ...) must not crash the
                # timer/state-change callback or leave batteries stuck mid
                # write — log it and let the next cycle retry cleanly rather
                # than propagating into HA's event loop.
                _LOGGER.exception("Anker Solix Energy Manager: control cycle failed")

    async def _run_cycle(self, *, is_periodic: bool) -> None:
        for battery in self.batteries:
            battery.refresh()

        now = time.monotonic()
        elapsed_s = (now - self._last_cycle_monotonic) if self._last_cycle_monotonic is not None else None
        self._last_cycle_monotonic = now

        battery_net_charge_w = sum(b.data.get("measured_power", 0.0) for b in self.batteries)
        self.consumption_tracker.accumulate(elapsed_s, battery_net_charge_w)
        await self.consumption_tracker.async_save()
        if self.price_tracker is not None:
            self.price_tracker.record_sample()

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

        # The command that was in effect while `raw_w` was measured — needed
        # by capacity protection to correctly project the effect of a *new*
        # command (see capacity_protection.py's docstring).
        previous_command_w = self.last_command_w

        updated_ts = state.last_updated.timestamp()
        is_stale_periodic = is_periodic and self._last_grid_updated == updated_ts
        self._last_grid_updated = updated_ts
        self.last_grid_power_w = raw_w

        if self.predictive_charging is not None:
            should_charge, reason = self.predictive_charging.should_charge()
            self.predictive_charging_active = should_charge
            self.predictive_charging_reason = reason
            if should_charge:
                await self._run_predictive_charge_cycle(raw_w, previous_command_w)
                return

        if is_stale_periodic:
            # Timer tick with no new sensor data since the last cycle: batteries
            # are already running the last-written command, nothing to do.
            return

        ev_adjustment = self.ev_exclusion.calculate_adjustment() if self.ev_exclusion else 0.0
        adjusted_raw_w = raw_w - ev_adjustment

        filtered_w = self.pd.filter_grid_sample(adjusted_raw_w, elapsed_s)
        result = self.pd.compute(
            grid_power_w=filtered_w,
            target_w=0.0,
            elapsed_s=elapsed_s,
            measured_battery_power_w=battery_net_charge_w,
        )

        if result.within_deadband:
            # Still guard the mode-revert quirk even when idling — it isn't
            # tied to whether we're actively commanding power.
            for battery in self.batteries:
                await battery.async_ensure_third_party_control()
            return

        power_w = result.power_w

        if self.ev_exclusion is not None and self.ev_exclusion.discharge_blocked and power_w < 0:
            power_w = 0
            self.pd.freeze(0, result.error_w)

        max_discharge_capacity_w = sum(b.max_discharge_w for b in self.batteries if b.data.get("available", False))
        power_w = apply_capacity_protection(
            power_w,
            raw_grid_w=raw_w,
            previous_power_w=previous_command_w,
            max_contracted_power_w=self.max_contracted_power_w,
            max_discharge_capacity_w=max_discharge_capacity_w,
        )
        if power_w != result.power_w:
            self.pd.previous_power = power_w  # keep PD state consistent with what was actually commanded

        await self._dispatch_power(power_w)

    async def _run_predictive_charge_cycle(self, raw_w: float, previous_command_w: float) -> None:
        target_w = self.predictive_charging.charge_power_w
        available = self.power_distribution.available_batteries(True)
        system_capacity = sum(b.max_charge_w for b in available)
        target_w = min(target_w, system_capacity)

        max_discharge_capacity_w = sum(b.max_discharge_w for b in self.batteries if b.data.get("available", False))
        target_w = apply_capacity_protection(
            target_w,
            raw_grid_w=raw_w,
            previous_power_w=previous_command_w,
            max_contracted_power_w=self.max_contracted_power_w,
            max_discharge_capacity_w=max_discharge_capacity_w,
        )
        # Keep PD state consistent so a return to normal control next cycle
        # starts from the real last-commanded power instead of stale state.
        self.pd.freeze(target_w, 0.0)
        await self._dispatch_power(target_w)

    async def _dispatch_power(self, power_w: float) -> None:
        is_charging = power_w > 0
        available = self.power_distribution.available_batteries(is_charging)
        magnitude = min(abs(power_w), sum((b.max_charge_w if is_charging else b.max_discharge_w) for b in available))

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


def _build_ev_exclusion(hass: HomeAssistant, entry: ConfigEntry) -> EVLoadExclusion | None:
    sensors = entry.data.get(CONF_EV_CHARGER_POWER_SENSORS) or []
    if not sensors:
        return None
    return EVLoadExclusion(
        hass=hass,
        charger_power_sensors=sensors,
        discharge_block_threshold_w=entry.data.get(CONF_EV_DISCHARGE_BLOCK_THRESHOLD_W, DEFAULT_EV_DISCHARGE_BLOCK_THRESHOLD_W),
    )


def _build_price_tracker(hass: HomeAssistant, entry: ConfigEntry) -> PriceTracker | None:
    sensor = entry.data.get(CONF_PRICE_SENSOR)
    if not sensor:
        return None
    return PriceTracker(
        hass=hass,
        price_sensor=sensor,
        forecast_attribute=entry.data.get(CONF_PRICE_FORECAST_ATTRIBUTE) or None,
        cheap_percentile=entry.data.get(CONF_PRICE_CHEAP_PERCENTILE, DEFAULT_PRICE_CHEAP_PERCENTILE),
    )


def _build_predictive_charging(
    hass: HomeAssistant,
    entry: ConfigEntry,
    consumption_tracker: ConsumptionTracker,
    price_tracker: PriceTracker | None,
    batteries: list[BatteryAdapter],
) -> PredictiveChargingManager | None:
    if not entry.data.get(CONF_PREDICTIVE_CHARGING_ENABLED):
        return None
    return PredictiveChargingManager(
        hass=hass,
        enabled=True,
        mode=entry.data[CONF_PREDICTIVE_CHARGING_MODE],
        target_soc=entry.data[CONF_PREDICTIVE_TARGET_SOC],
        coverage_hours=entry.data[CONF_PREDICTIVE_COVERAGE_HOURS],
        charge_power_w=entry.data[CONF_PREDICTIVE_CHARGE_POWER_W],
        fixed_slots=entry.data.get(CONF_PREDICTIVE_FIXED_SLOTS) or [],
        solar_forecast_remaining_sensor=entry.data.get(CONF_SOLAR_FORECAST_REMAINING_SENSOR),
        consumption_tracker=consumption_tracker,
        price_tracker=price_tracker,
        batteries=batteries,
    )


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    batteries = [BatteryAdapter(hass=hass, config=b) for b in entry.data[CONF_BATTERIES]]
    for b in batteries:
        b.refresh()

    consumption_tracker = ConsumptionTracker(
        hass=hass,
        entry_id=entry.entry_id,
        solar_power_sensor=entry.data.get(CONF_SOLAR_POWER_SENSOR),
        grid_power_sensor=entry.data[CONF_GRID_POWER_SENSOR],
    )
    await consumption_tracker.async_load()

    price_tracker = _build_price_tracker(hass, entry)
    ev_exclusion = _build_ev_exclusion(hass, entry)
    predictive_charging = _build_predictive_charging(hass, entry, consumption_tracker, price_tracker, batteries)

    controller = EnergyManagerController(
        hass=hass,
        entry=entry,
        batteries=batteries,
        pd=PDController(**_pd_kwargs_from_entry(entry)),
        power_distribution=PowerDistribution(batteries=batteries),
        consumption_tracker=consumption_tracker,
        ev_exclusion=ev_exclusion,
        price_tracker=price_tracker,
        predictive_charging=predictive_charging,
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

    async def _async_shutdown(_event=None) -> None:
        await consumption_tracker.async_save(force=True)

    entry.async_on_unload(hass.bus.async_listen_once("homeassistant_stop", _async_shutdown))

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
        controller: EnergyManagerController = hass.data[DOMAIN][entry.entry_id]
        await controller.consumption_tracker.async_save(force=True)
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unloaded
