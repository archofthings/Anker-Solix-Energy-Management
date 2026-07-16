"""Live-tunable PD controller gains.

Mirrors the reference project's approach: PD parameters are entities, not
one-time config-flow choices, so they can be tuned from the dashboard while
watching the PD Control Quality sensors and hot-reload immediately. Values
persist to config_entry.options so they survive a restart.
"""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EnergyManagerController
from .const import (
    CONF_PD_DEADBAND,
    CONF_PD_DIRECTION_HYSTERESIS,
    CONF_PD_KD,
    CONF_PD_KP,
    CONF_PD_MAX_POWER_CHANGE,
    DOMAIN,
)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Anker Solix Energy Manager", manufacturer="archofthings")


class _PDGainNumber(NumberEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, controller: EnergyManagerController, entry: ConfigEntry, key: str, attr: str, name: str, *, min_v, max_v, step):
        self._controller = controller
        self._entry = entry
        self._conf_key = key
        self._attr_name = name
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_native_min_value = min_v
        self._attr_native_max_value = max_v
        self._attr_native_step = step
        self._pd_attr = attr
        self._attr_device_info = _device_info(entry)

    @property
    def native_value(self):
        return getattr(self._controller.pd, self._pd_attr)

    async def async_set_native_value(self, value: float) -> None:
        setattr(self._controller.pd, self._pd_attr, value)
        new_options = {**self._entry.options, self._conf_key: value}
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    controller: EnergyManagerController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [
            _PDGainNumber(controller, entry, CONF_PD_KP, "kp", "PD Kp (proportional gain)", min_v=0, max_v=2, step=0.01),
            _PDGainNumber(controller, entry, CONF_PD_KD, "kd", "PD Kd (derivative gain)", min_v=0, max_v=2, step=0.01),
            _PDGainNumber(controller, entry, CONF_PD_DEADBAND, "deadband_w", "PD Deadband", min_v=0, max_v=500, step=5),
            _PDGainNumber(controller, entry, CONF_PD_MAX_POWER_CHANGE, "max_power_change_w", "PD Max Power Change per Cycle", min_v=50, max_v=3500, step=25),
            _PDGainNumber(controller, entry, CONF_PD_DIRECTION_HYSTERESIS, "direction_hysteresis_w", "PD Direction Hysteresis", min_v=0, max_v=1000, step=10),
        ]
    )
