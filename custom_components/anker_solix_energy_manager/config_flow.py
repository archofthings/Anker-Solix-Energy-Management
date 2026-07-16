"""Config flow: collect the grid sensor plus the entity IDs of exactly two
Anker Solix batteries (already set up via the official ha-anker-solix-official
integration). No host/port/Modbus config here — see const.py's module note.

PD tuning is intentionally not part of this flow: it is exposed as live
number/select entities (see number.py / select.py) so it can be adjusted and
hot-reloaded without reconfiguring the integration.

Supports both a fresh setup (`async_step_user`) and reconfiguring an
existing entry (`async_step_reconfigure`) through the exact same three
steps — the only differences are: the unique-id/already-configured guard is
skipped, every field is pre-filled from the entry's current data instead of
hardcoded defaults, and the flow ends by updating + reloading the entry
instead of creating a new one. Without this, fixing a single wrong entity ID
would mean deleting and re-adding the whole integration — which would also
orphan the accumulated 7-day consumption history (keyed by the old entry_id).
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
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
    CONF_BATTERIES,
    CONF_EV_CHARGER_POWER_SENSORS,
    CONF_EV_DISCHARGE_BLOCK_THRESHOLD_W,
    CONF_GRID_POWER_SENSOR,
    CONF_MAX_CONTRACTED_POWER,
    CONF_MIN_CYCLE_INTERVAL,
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
    DEFAULT_BATTERY_CAPACITY_WH,
    DEFAULT_BATTERY_MAX_POWER_W,
    DEFAULT_EV_DISCHARGE_BLOCK_THRESHOLD_W,
    DEFAULT_MAX_CONTRACTED_POWER,
    DEFAULT_MIN_CYCLE_INTERVAL,
    DEFAULT_PREDICTIVE_CHARGE_POWER_W,
    DEFAULT_PREDICTIVE_COVERAGE_HOURS,
    DEFAULT_PREDICTIVE_TARGET_SOC,
    DEFAULT_PRICE_CHEAP_PERCENTILE,
    DOMAIN,
    PREDICTIVE_MODE_FIXED_SLOTS,
    PREDICTIVE_MODE_OPTIONS,
)

BATTERY_COUNT = 2
# The entity-id fields checked for accidental duplicate selection between
# the two batteries (picking the same physical unit's entity twice).
_BATTERY_ENTITY_FIELDS = (
    BATTERY_OPERATING_MODE_ENTITY,
    BATTERY_TARGET_GRID_POWER_ENTITY,
    BATTERY_GRID_FLOW_ENTITY,
    BATTERY_SOC_ENTITY,
    BATTERY_DEVICE_STATUS_ENTITY,
    BATTERY_CHARGING_POWER_ENTITY,
    BATTERY_DISCHARGING_POWER_ENTITY,
)


def _power_number(*, min_value: float = 0) -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=min_value, max=20000, step=1, unit_of_measurement="W", mode=selector.NumberSelectorMode.BOX)
    )


class AnkerSolixEnergyManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict = {}
        self._batteries: list[dict] = []
        self._existing_batteries: list[dict] = []  # reconfigure prefill source only
        self._battery_index = 1

    @property
    def _is_reconfigure(self) -> bool:
        return self.source == config_entries.SOURCE_RECONFIGURE

    async def async_step_reconfigure(self, _user_input: dict | None = None):
        entry = self._get_reconfigure_entry()
        self._data = dict(entry.data)
        self._existing_batteries = list(entry.data.get(CONF_BATTERIES, []))
        self._batteries = []
        self._battery_index = 1
        return await self.async_step_user()

    async def async_step_user(self, user_input: dict | None = None):
        if not self._is_reconfigure:
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            self._data[CONF_GRID_POWER_SENSOR] = user_input[CONF_GRID_POWER_SENSOR]
            self._data[CONF_MAX_CONTRACTED_POWER] = user_input[CONF_MAX_CONTRACTED_POWER]
            self._data[CONF_MIN_CYCLE_INTERVAL] = user_input[CONF_MIN_CYCLE_INTERVAL]
            self._battery_index = 1
            self._batteries = []
            return await self.async_step_battery()

        schema = vol.Schema(
            {
                vol.Required(CONF_GRID_POWER_SENSOR, default=self._data.get(CONF_GRID_POWER_SENSOR)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(
                    CONF_MAX_CONTRACTED_POWER, default=self._data.get(CONF_MAX_CONTRACTED_POWER, DEFAULT_MAX_CONTRACTED_POWER)
                ): _power_number(min_value=500),
                vol.Required(
                    CONF_MIN_CYCLE_INTERVAL, default=self._data.get(CONF_MIN_CYCLE_INTERVAL, DEFAULT_MIN_CYCLE_INTERVAL)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=30, step=0.5, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_battery(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        idx = self._battery_index
        existing = self._existing_batteries[idx - 1] if idx - 1 < len(self._existing_batteries) else {}

        if user_input is not None:
            name = user_input[BATTERY_NAME].strip()
            other_names = {b[BATTERY_NAME].strip().lower() for b in self._batteries}
            entity_ids = {user_input[f] for f in _BATTERY_ENTITY_FIELDS}
            other_entity_ids: set[str] = set()
            for b in self._batteries:
                other_entity_ids.update(b[f] for f in _BATTERY_ENTITY_FIELDS)

            if not name:
                errors["base"] = "battery_name_required"
            elif name.lower() in other_names:
                errors["base"] = "battery_name_duplicate"
            elif entity_ids & other_entity_ids:
                errors["base"] = "battery_entity_duplicate"
            else:
                battery_cfg = {
                    BATTERY_NAME: name,
                    BATTERY_OPERATING_MODE_ENTITY: user_input[BATTERY_OPERATING_MODE_ENTITY],
                    BATTERY_TARGET_GRID_POWER_ENTITY: user_input[BATTERY_TARGET_GRID_POWER_ENTITY],
                    BATTERY_GRID_FLOW_ENTITY: user_input[BATTERY_GRID_FLOW_ENTITY],
                    BATTERY_SOC_ENTITY: user_input[BATTERY_SOC_ENTITY],
                    BATTERY_DEVICE_STATUS_ENTITY: user_input[BATTERY_DEVICE_STATUS_ENTITY],
                    BATTERY_CHARGING_POWER_ENTITY: user_input[BATTERY_CHARGING_POWER_ENTITY],
                    BATTERY_DISCHARGING_POWER_ENTITY: user_input[BATTERY_DISCHARGING_POWER_ENTITY],
                    BATTERY_CAPACITY_WH: user_input[BATTERY_CAPACITY_WH],
                    BATTERY_MAX_CHARGE_W: user_input[BATTERY_MAX_CHARGE_W],
                    BATTERY_MAX_DISCHARGE_W: user_input[BATTERY_MAX_DISCHARGE_W],
                }
                self._batteries.append(battery_cfg)

                if idx < BATTERY_COUNT:
                    self._battery_index += 1
                    return await self.async_step_battery()

                self._data[CONF_BATTERIES] = self._batteries
                return await self.async_step_features()

        schema = vol.Schema(
            {
                vol.Required(BATTERY_NAME, default=existing.get(BATTERY_NAME, f"Solarbank {idx}")): str,
                vol.Required(
                    BATTERY_OPERATING_MODE_ENTITY, default=existing.get(BATTERY_OPERATING_MODE_ENTITY)
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="select")),
                vol.Required(
                    BATTERY_TARGET_GRID_POWER_ENTITY, default=existing.get(BATTERY_TARGET_GRID_POWER_ENTITY)
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="number")),
                vol.Required(
                    BATTERY_GRID_FLOW_ENTITY, default=existing.get(BATTERY_GRID_FLOW_ENTITY)
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="select")),
                vol.Required(BATTERY_SOC_ENTITY, default=existing.get(BATTERY_SOC_ENTITY)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(
                    BATTERY_DEVICE_STATUS_ENTITY, default=existing.get(BATTERY_DEVICE_STATUS_ENTITY)
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required(
                    BATTERY_CHARGING_POWER_ENTITY, default=existing.get(BATTERY_CHARGING_POWER_ENTITY)
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required(
                    BATTERY_DISCHARGING_POWER_ENTITY, default=existing.get(BATTERY_DISCHARGING_POWER_ENTITY)
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required(
                    BATTERY_CAPACITY_WH, default=existing.get(BATTERY_CAPACITY_WH, DEFAULT_BATTERY_CAPACITY_WH)
                ): _power_number(min_value=100),
                vol.Required(
                    BATTERY_MAX_CHARGE_W, default=existing.get(BATTERY_MAX_CHARGE_W, DEFAULT_BATTERY_MAX_POWER_W)
                ): _power_number(min_value=100),
                vol.Required(
                    BATTERY_MAX_DISCHARGE_W, default=existing.get(BATTERY_MAX_DISCHARGE_W, DEFAULT_BATTERY_MAX_POWER_W)
                ): _power_number(min_value=100),
            }
        )
        return self.async_show_form(
            step_id="battery",
            data_schema=schema,
            errors=errors,
            description_placeholders={"index": str(idx), "count": str(BATTERY_COUNT)},
        )

    async def async_step_features(self, user_input: dict | None = None):
        """Optional Phase 2 features: everything here can be left blank to
        keep that feature disabled. Only a single fixed charging slot is
        configurable through the UI; edit the config entry data directly
        for more than one."""
        errors: dict[str, str] = {}
        if user_input is not None:
            self._data[CONF_SOLAR_POWER_SENSOR] = user_input.get(CONF_SOLAR_POWER_SENSOR)
            self._data[CONF_SOLAR_FORECAST_REMAINING_SENSOR] = user_input.get(CONF_SOLAR_FORECAST_REMAINING_SENSOR)

            ev_sensors = [s for s in (user_input.get("ev_charger_1"), user_input.get("ev_charger_2")) if s]
            self._data[CONF_EV_CHARGER_POWER_SENSORS] = ev_sensors
            self._data[CONF_EV_DISCHARGE_BLOCK_THRESHOLD_W] = user_input[CONF_EV_DISCHARGE_BLOCK_THRESHOLD_W]

            self._data[CONF_PRICE_SENSOR] = user_input.get(CONF_PRICE_SENSOR)
            self._data[CONF_PRICE_FORECAST_ATTRIBUTE] = user_input.get(CONF_PRICE_FORECAST_ATTRIBUTE)
            self._data[CONF_PRICE_CHEAP_PERCENTILE] = user_input[CONF_PRICE_CHEAP_PERCENTILE]

            predictive_enabled = user_input[CONF_PREDICTIVE_CHARGING_ENABLED]
            predictive_mode = user_input[CONF_PREDICTIVE_CHARGING_MODE]
            slot_start, slot_end = user_input.get("fixed_slot_start"), user_input.get("fixed_slot_end")

            if predictive_enabled and not user_input.get(CONF_SOLAR_FORECAST_REMAINING_SENSOR):
                errors["base"] = "predictive_needs_solar_forecast"
            elif predictive_enabled and predictive_mode != PREDICTIVE_MODE_FIXED_SLOTS and not user_input.get(CONF_PRICE_SENSOR):
                errors["base"] = "predictive_needs_price_sensor"
            elif predictive_enabled and predictive_mode == PREDICTIVE_MODE_FIXED_SLOTS and not (slot_start and slot_end):
                errors["base"] = "predictive_needs_fixed_slot"
            else:
                self._data[CONF_PREDICTIVE_CHARGING_ENABLED] = predictive_enabled
                self._data[CONF_PREDICTIVE_CHARGING_MODE] = predictive_mode
                self._data[CONF_PREDICTIVE_TARGET_SOC] = user_input[CONF_PREDICTIVE_TARGET_SOC]
                self._data[CONF_PREDICTIVE_COVERAGE_HOURS] = user_input[CONF_PREDICTIVE_COVERAGE_HOURS]
                self._data[CONF_PREDICTIVE_CHARGE_POWER_W] = user_input[CONF_PREDICTIVE_CHARGE_POWER_W]

                slots = []
                if slot_start and slot_end:
                    slots.append({"start": slot_start[:5], "end": slot_end[:5]})
                self._data[CONF_PREDICTIVE_FIXED_SLOTS] = slots

                if self._is_reconfigure:
                    return self.async_update_reload_and_abort(self._get_reconfigure_entry(), data=self._data)
                return self.async_create_entry(title="Anker Solix Energy Manager", data=self._data)

        current = self._data
        current_slots = current.get(CONF_PREDICTIVE_FIXED_SLOTS) or []
        default_slot_start = current_slots[0]["start"] if current_slots else None
        default_slot_end = current_slots[0]["end"] if current_slots else None
        current_ev_sensors = current.get(CONF_EV_CHARGER_POWER_SENSORS) or []

        def _opt(key, value):
            # A plain vol.Optional(key) — no `default` kwarg at all — leaves
            # a key missing from submitted input un-validated and absent
            # from the result, which is what every one of these fields
            # needs when there's nothing to pre-fill. Passing
            # `default=None` instead (e.g. via current.get(key), which
            # returns None until first configured) makes voluptuous inject
            # and then *validate* that None through the EntitySelector,
            # which rejects it — so the default can only be added when
            # there's a genuine value to pre-fill.
            return vol.Optional(key, default=value) if value else vol.Optional(key)

        schema = vol.Schema(
            {
                _opt(CONF_SOLAR_POWER_SENSOR, current.get(CONF_SOLAR_POWER_SENSOR)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                _opt(
                    CONF_SOLAR_FORECAST_REMAINING_SENSOR, current.get(CONF_SOLAR_FORECAST_REMAINING_SENSOR)
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                _opt(
                    "ev_charger_1", current_ev_sensors[0] if current_ev_sensors else None
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                _opt(
                    "ev_charger_2", current_ev_sensors[1] if len(current_ev_sensors) > 1 else None
                ): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required(
                    CONF_EV_DISCHARGE_BLOCK_THRESHOLD_W,
                    default=current.get(CONF_EV_DISCHARGE_BLOCK_THRESHOLD_W, DEFAULT_EV_DISCHARGE_BLOCK_THRESHOLD_W),
                ): _power_number(),
                _opt(CONF_PRICE_SENSOR, current.get(CONF_PRICE_SENSOR)): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                _opt(CONF_PRICE_FORECAST_ATTRIBUTE, current.get(CONF_PRICE_FORECAST_ATTRIBUTE)): str,
                vol.Required(
                    CONF_PRICE_CHEAP_PERCENTILE, default=current.get(CONF_PRICE_CHEAP_PERCENTILE, DEFAULT_PRICE_CHEAP_PERCENTILE)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=100, step=1, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(
                    CONF_PREDICTIVE_CHARGING_ENABLED, default=current.get(CONF_PREDICTIVE_CHARGING_ENABLED, False)
                ): selector.BooleanSelector(),
                vol.Required(
                    CONF_PREDICTIVE_CHARGING_MODE, default=current.get(CONF_PREDICTIVE_CHARGING_MODE, PREDICTIVE_MODE_FIXED_SLOTS)
                ): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=PREDICTIVE_MODE_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(
                    CONF_PREDICTIVE_TARGET_SOC, default=current.get(CONF_PREDICTIVE_TARGET_SOC, DEFAULT_PREDICTIVE_TARGET_SOC)
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(
                    CONF_PREDICTIVE_COVERAGE_HOURS,
                    default=current.get(CONF_PREDICTIVE_COVERAGE_HOURS, DEFAULT_PREDICTIVE_COVERAGE_HOURS),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=24, step=0.5, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(
                    CONF_PREDICTIVE_CHARGE_POWER_W,
                    default=current.get(CONF_PREDICTIVE_CHARGE_POWER_W, DEFAULT_PREDICTIVE_CHARGE_POWER_W),
                ): _power_number(),
                _opt("fixed_slot_start", default_slot_start): selector.TimeSelector(),
                _opt("fixed_slot_end", default_slot_end): selector.TimeSelector(),
            }
        )
        return self.async_show_form(step_id="features", data_schema=schema, errors=errors)
