"""Adapter between the PD/load-sharing decision logic and the official
`ha-anker-solix-official` integration's entities.

This is the entire seam: decision logic reads `BatteryAdapter.data` (a plain
dict) and writes through `BatteryAdapter.async_set_power()`. Nothing
upstream of this file needs to know these are Anker entities, or about the
mode-revert quirk below.

Known Anker quirk (must not be "fixed away"): the units silently revert from
third_party_control back to their native operating mode on a :07/:37 minute
wall-clock cycle. The fix that's been confirmed to work is to never set
third_party_control directly — always route through custom_mode first. See
`async_ensure_third_party_control()`.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

from homeassistant.core import HomeAssistant

from .const import (
    ANKER_TARGET_GRID_POWER_FLOOR_W,
    ANKER_TARGET_GRID_POWER_MAX_W,
    BATTERY_CAPACITY_WH,
    BATTERY_CHARGING_POWER_ENTITY,
    BATTERY_DEVICE_STATUS_ENTITY,
    BATTERY_DISCHARGING_POWER_ENTITY,
    BATTERY_GRID_FLOW_ENTITY,
    BATTERY_MAX_CHARGE_W,
    BATTERY_MAX_DISCHARGE_W,
    BATTERY_NAME,
    BATTERY_OPERATING_MODE_ENTITY,
    BATTERY_SOC_ENTITY,
    BATTERY_TARGET_GRID_POWER_ENTITY,
    GRID_FLOW_CHARGE,
    GRID_FLOW_DISCHARGE,
    MODE_CUSTOM,
    MODE_GUARD_CHECK_INTERVAL_S,
    MODE_THIRD_PARTY_CONTROL,
)

_LOGGER = logging.getLogger(__name__)


def _state_float(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable", None):
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


@dataclass(eq=False)
class BatteryAdapter:
    """One instance per configured battery. Mirrors the coordinator.data
    dict shape the ported decision modules (power_distribution, pd_controller)
    expect, backed by live Anker entity state instead of Modbus registers.

    `eq=False` keeps the default identity-based __eq__/__hash__ instead of
    the dataclass-generated field-value-based ones — power_distribution.py
    uses BatteryAdapter instances as dict keys and in membership checks
    keyed on "is this the same battery object", not on field equality
    (which would also break the moment `data` is mutated by refresh()).
    """

    hass: HomeAssistant
    config: dict[str, Any]
    data: dict[str, Any] = field(default_factory=dict)
    # -inf (not 0.0) guarantees the very first guard check always runs,
    # regardless of what time.monotonic()'s arbitrary reference point
    # happens to be on this platform — relying on "monotonic() is probably
    # already > 30" by the time this loads was true by accident, not design.
    _last_mode_guard_check: float = float("-inf")

    @property
    def name(self) -> str:
        return self.config[BATTERY_NAME]

    @property
    def capacity_wh(self) -> float:
        return self.config[BATTERY_CAPACITY_WH]

    @property
    def max_charge_w(self) -> float:
        return self.config[BATTERY_MAX_CHARGE_W]

    @property
    def max_discharge_w(self) -> float:
        return self.config[BATTERY_MAX_DISCHARGE_W]

    # -- read side ----------------------------------------------------------

    def refresh(self) -> None:
        """Reshape current Anker entity state into `self.data`.

        Kept as a plain dict (not typed) deliberately — the decision modules
        (power_distribution.py, pd_controller.py) read it as
        `data.get("battery_soc", ...)` and don't need a typed model.
        """
        hass = self.hass
        cfg = self.config

        soc = _state_float(hass, cfg.get(BATTERY_SOC_ENTITY))
        charging_power_raw = _state_float(hass, cfg.get(BATTERY_CHARGING_POWER_ENTITY))
        discharging_power_raw = _state_float(hass, cfg.get(BATTERY_DISCHARGING_POWER_ENTITY))
        status_state = hass.states.get(cfg.get(BATTERY_DEVICE_STATUS_ENTITY) or "")
        mode_state = hass.states.get(cfg.get(BATTERY_OPERATING_MODE_ENTITY) or "")

        charging_power = charging_power_raw or 0.0
        discharging_power = discharging_power_raw or 0.0

        self.data = {
            "battery_soc": soc if soc is not None else 50.0,
            "charging_power": charging_power,
            "discharging_power": discharging_power,
            # signed measured AC power: +charging / -discharging, for PD anti-windup
            "measured_power": charging_power - discharging_power,
            "device_status": status_state.state if status_state else None,
            "operating_mode": mode_state.state if mode_state else None,
            # A missing/unavailable charging or discharging power sensor must
            # NOT silently read as "0W measured" — that would feed a false
            # reading straight into the PD anti-windup re-anchor logic
            # (looks identical to "battery genuinely delivered nothing").
            # Treat the whole battery as unavailable instead, so it's
            # excluded from selection until the sensor is back.
            "available": (
                soc is not None
                and status_state is not None
                and mode_state is not None
                and charging_power_raw is not None
                and discharging_power_raw is not None
            ),
        }

    # -- write side -----------------------------------------------------

    async def async_ensure_third_party_control(self, *, force: bool = False) -> None:
        """Guard against the silent mode-revert quirk.

        Checked at most every MODE_GUARD_CHECK_INTERVAL_S unless `force` is
        set (e.g. right before the very first write of a session). Anker
        units have been observed reverting third_party_control -> their
        native mode on a :07/:37 wall-clock cycle; simply re-selecting
        third_party_control directly does not reliably stick, but routing
        through custom_mode first does.
        """
        now = time.monotonic()
        if not force and (now - self._last_mode_guard_check) < MODE_GUARD_CHECK_INTERVAL_S:
            return
        self._last_mode_guard_check = now

        entity_id = self.config.get(BATTERY_OPERATING_MODE_ENTITY)
        if not entity_id:
            return
        state = self.hass.states.get(entity_id)
        current = state.state if state else None
        if current == MODE_THIRD_PARTY_CONTROL:
            return

        _LOGGER.info(
            "%s: operating_mode is %r, not %r — re-applying via custom_mode first (quirk guard)",
            self.name, current, MODE_THIRD_PARTY_CONTROL,
        )
        await self.hass.services.async_call(
            "select", "select_option",
            {"entity_id": entity_id, "option": MODE_CUSTOM},
            blocking=True,
        )
        await self.hass.services.async_call(
            "select", "select_option",
            {"entity_id": entity_id, "option": MODE_THIRD_PARTY_CONTROL},
            blocking=True,
        )

    async def async_set_power(self, charge_w: float, discharge_w: float) -> None:
        """Write a charge/discharge setpoint. Exactly one of charge_w /
        discharge_w should be non-zero.
        """
        await self.async_ensure_third_party_control()

        cfg = self.config
        grid_flow_entity = cfg.get(BATTERY_GRID_FLOW_ENTITY)
        power_entity = cfg.get(BATTERY_TARGET_GRID_POWER_ENTITY)
        if not power_entity:
            _LOGGER.warning("%s: no target_grid_power entity configured, skipping write", self.name)
            return

        net_w = charge_w - discharge_w
        if abs(net_w) < ANKER_TARGET_GRID_POWER_FLOOR_W:
            await self._async_write_power(power_entity, 0)
            return

        magnitude = min(abs(net_w), ANKER_TARGET_GRID_POWER_MAX_W)
        direction = GRID_FLOW_CHARGE if net_w > 0 else GRID_FLOW_DISCHARGE

        if grid_flow_entity:
            flow_state = self.hass.states.get(grid_flow_entity)
            direction_changing = flow_state is None or flow_state.state != direction
            if direction_changing:
                # Zero the setpoint *before* flipping direction. Otherwise
                # there's a real window — between these two service calls
                # landing on the device — where grid_flow already reads the
                # new direction while target_grid_power still holds the old
                # (possibly large) magnitude from the previous direction,
                # which the device could act on immediately.
                await self._async_write_power(power_entity, 0)
                await self.hass.services.async_call(
                    "select", "select_option",
                    {"entity_id": grid_flow_entity, "option": direction},
                    blocking=True,
                )

        await self._async_write_power(power_entity, magnitude)

    async def _async_write_power(self, entity_id: str, value_w: float) -> None:
        current = _state_float(self.hass, entity_id)
        rounded = round(value_w)
        if current is not None and abs(current - rounded) < 1:
            return  # no-op write avoidance — don't hammer the Modbus TCP link
        await self.hass.services.async_call(
            "number", "set_value",
            {"entity_id": entity_id, "value": rounded},
            blocking=True,
        )
