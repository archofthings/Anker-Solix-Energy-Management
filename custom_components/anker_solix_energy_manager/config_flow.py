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
    CONF_GRID_POWER_SENSOR,
    CONF_MAX_CONTRACTED_POWER,
    CONF_MIN_CYCLE_INTERVAL,
    DEFAULT_BATTERY_CAPACITY_WH,
    DEFAULT_BATTERY_MAX_POWER_W,
    DEFAULT_MAX_CONTRACTED_POWER,
    DEFAULT_MIN_CYCLE_INTERVAL,
    DOMAIN,
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
            return self.async_create_entry(title="Anker Solix Energy Manager", data=self._data)

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
