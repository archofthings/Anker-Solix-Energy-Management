"""Config flow: collect the grid sensor plus the entity IDs of exactly two
Anker Solix batteries (already set up via the official ha-anker-solix-official
integration). No host/port/Modbus config here — see const.py's module note.

PD tuning is intentionally not part of this flow: it is exposed as live
number/select entities (see number.py / select.py) so it can be adjusted and
hot-reloaded without reconfiguring the integration, matching how the
reference project treats PD gains as tunable entities rather than one-time
setup values.
"""
from __future__ import annotations

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers import selector

from .const import (
    BATTERY_CAPACITY_WH,
    BATTERY_CHARGE_LIMIT_ENTITY,
    BATTERY_CHARGING_POWER_ENTITY,
    BATTERY_DEVICE_STATUS_ENTITY,
    BATTERY_DISCHARGE_LIMIT_ENTITY,
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


def _power_number() -> selector.NumberSelector:
    return selector.NumberSelector(
        selector.NumberSelectorConfig(min=0, max=20000, step=1, unit_of_measurement="W", mode=selector.NumberSelectorMode.BOX)
    )


class AnkerSolixEnergyManagerConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._data: dict = {}
        self._batteries: list[dict] = []
        self._battery_index = 1

    async def async_step_user(self, user_input: dict | None = None):
        await self.async_set_unique_id(DOMAIN)
        self._abort_if_unique_id_configured()

        errors: dict[str, str] = {}
        if user_input is not None:
            self._data[CONF_GRID_POWER_SENSOR] = user_input[CONF_GRID_POWER_SENSOR]
            self._data[CONF_MAX_CONTRACTED_POWER] = user_input[CONF_MAX_CONTRACTED_POWER]
            self._data[CONF_MIN_CYCLE_INTERVAL] = user_input[CONF_MIN_CYCLE_INTERVAL]
            self._battery_index = 1
            return await self.async_step_battery()

        schema = vol.Schema(
            {
                vol.Required(CONF_GRID_POWER_SENSOR): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(CONF_MAX_CONTRACTED_POWER, default=DEFAULT_MAX_CONTRACTED_POWER): _power_number(),
                vol.Required(CONF_MIN_CYCLE_INTERVAL, default=DEFAULT_MIN_CYCLE_INTERVAL): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=30, step=0.5, unit_of_measurement="s", mode=selector.NumberSelectorMode.BOX)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_battery(self, user_input: dict | None = None):
        errors: dict[str, str] = {}
        idx = self._battery_index

        if user_input is not None:
            battery_cfg = {
                BATTERY_NAME: user_input[BATTERY_NAME],
                BATTERY_OPERATING_MODE_ENTITY: user_input[BATTERY_OPERATING_MODE_ENTITY],
                BATTERY_TARGET_GRID_POWER_ENTITY: user_input[BATTERY_TARGET_GRID_POWER_ENTITY],
                BATTERY_GRID_FLOW_ENTITY: user_input[BATTERY_GRID_FLOW_ENTITY],
                BATTERY_SOC_ENTITY: user_input[BATTERY_SOC_ENTITY],
                BATTERY_DEVICE_STATUS_ENTITY: user_input[BATTERY_DEVICE_STATUS_ENTITY],
                BATTERY_CHARGING_POWER_ENTITY: user_input[BATTERY_CHARGING_POWER_ENTITY],
                BATTERY_DISCHARGING_POWER_ENTITY: user_input[BATTERY_DISCHARGING_POWER_ENTITY],
                BATTERY_CHARGE_LIMIT_ENTITY: user_input.get(BATTERY_CHARGE_LIMIT_ENTITY),
                BATTERY_DISCHARGE_LIMIT_ENTITY: user_input.get(BATTERY_DISCHARGE_LIMIT_ENTITY),
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
                vol.Required(BATTERY_NAME, default=f"Solarbank {idx}"): str,
                vol.Required(BATTERY_OPERATING_MODE_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="select")
                ),
                vol.Required(BATTERY_TARGET_GRID_POWER_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="number")
                ),
                vol.Required(BATTERY_GRID_FLOW_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="select")
                ),
                vol.Required(BATTERY_SOC_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(BATTERY_DEVICE_STATUS_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(BATTERY_CHARGING_POWER_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Required(BATTERY_DISCHARGING_POWER_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="sensor")
                ),
                vol.Optional(BATTERY_CHARGE_LIMIT_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="number")
                ),
                vol.Optional(BATTERY_DISCHARGE_LIMIT_ENTITY): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="number")
                ),
                vol.Required(BATTERY_CAPACITY_WH, default=DEFAULT_BATTERY_CAPACITY_WH): _power_number(),
                vol.Required(BATTERY_MAX_CHARGE_W, default=DEFAULT_BATTERY_MAX_POWER_W): _power_number(),
                vol.Required(BATTERY_MAX_DISCHARGE_W, default=DEFAULT_BATTERY_MAX_POWER_W): _power_number(),
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
            if predictive_enabled and not user_input.get(CONF_SOLAR_FORECAST_REMAINING_SENSOR):
                errors["base"] = "predictive_needs_solar_forecast"
            elif predictive_enabled and user_input[CONF_PREDICTIVE_CHARGING_MODE] != PREDICTIVE_MODE_FIXED_SLOTS and not user_input.get(CONF_PRICE_SENSOR):
                errors["base"] = "predictive_needs_price_sensor"
            else:
                self._data[CONF_PREDICTIVE_CHARGING_ENABLED] = predictive_enabled
                self._data[CONF_PREDICTIVE_CHARGING_MODE] = user_input[CONF_PREDICTIVE_CHARGING_MODE]
                self._data[CONF_PREDICTIVE_TARGET_SOC] = user_input[CONF_PREDICTIVE_TARGET_SOC]
                self._data[CONF_PREDICTIVE_COVERAGE_HOURS] = user_input[CONF_PREDICTIVE_COVERAGE_HOURS]
                self._data[CONF_PREDICTIVE_CHARGE_POWER_W] = user_input[CONF_PREDICTIVE_CHARGE_POWER_W]

                slots = []
                start, end = user_input.get("fixed_slot_start"), user_input.get("fixed_slot_end")
                if start and end:
                    slots.append({"start": start[:5], "end": end[:5]})
                self._data[CONF_PREDICTIVE_FIXED_SLOTS] = slots

                return self.async_create_entry(title="Anker Solix Energy Manager", data=self._data)

        schema = vol.Schema(
            {
                vol.Optional(CONF_SOLAR_POWER_SENSOR): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Optional(CONF_SOLAR_FORECAST_REMAINING_SENSOR): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Optional("ev_charger_1"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Optional("ev_charger_2"): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Required(CONF_EV_DISCHARGE_BLOCK_THRESHOLD_W, default=DEFAULT_EV_DISCHARGE_BLOCK_THRESHOLD_W): _power_number(),
                vol.Optional(CONF_PRICE_SENSOR): selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor")),
                vol.Optional(CONF_PRICE_FORECAST_ATTRIBUTE): str,
                vol.Required(CONF_PRICE_CHEAP_PERCENTILE, default=DEFAULT_PRICE_CHEAP_PERCENTILE): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=100, step=1, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_PREDICTIVE_CHARGING_ENABLED, default=False): selector.BooleanSelector(),
                vol.Required(CONF_PREDICTIVE_CHARGING_MODE, default=PREDICTIVE_MODE_FIXED_SLOTS): selector.SelectSelector(
                    selector.SelectSelectorConfig(options=PREDICTIVE_MODE_OPTIONS, mode=selector.SelectSelectorMode.DROPDOWN)
                ),
                vol.Required(CONF_PREDICTIVE_TARGET_SOC, default=DEFAULT_PREDICTIVE_TARGET_SOC): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=0, max=100, step=1, unit_of_measurement="%", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_PREDICTIVE_COVERAGE_HOURS, default=DEFAULT_PREDICTIVE_COVERAGE_HOURS): selector.NumberSelector(
                    selector.NumberSelectorConfig(min=1, max=24, step=0.5, unit_of_measurement="h", mode=selector.NumberSelectorMode.BOX)
                ),
                vol.Required(CONF_PREDICTIVE_CHARGE_POWER_W, default=DEFAULT_PREDICTIVE_CHARGE_POWER_W): _power_number(),
                vol.Optional("fixed_slot_start"): selector.TimeSelector(),
                vol.Optional("fixed_slot_end"): selector.TimeSelector(),
            }
        )
        return self.async_show_form(step_id="features", data_schema=schema, errors=errors)
