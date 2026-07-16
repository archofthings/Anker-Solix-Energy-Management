"""PD tuning profile selector — applies a Kp/Kd/max-power-change preset."""
from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EnergyManagerController
from .const import (
    CONF_PD_KD,
    CONF_PD_KP,
    CONF_PD_MAX_POWER_CHANGE,
    CONF_PD_TUNING_PROFILE,
    DEFAULT_PD_TUNING_PROFILE,
    DOMAIN,
    PD_PROFILE_CUSTOM,
    PD_TUNING_PROFILE_OPTIONS,
    PD_TUNING_PROFILES,
)


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Anker Solix Energy Manager", manufacturer="archofthings")


class PDTuningProfileSelect(SelectEntity):
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:tune-vertical"
    _attr_options = PD_TUNING_PROFILE_OPTIONS

    def __init__(self, controller: EnergyManagerController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._entry = entry
        self._attr_name = "PD Tuning Profile"
        self._attr_unique_id = f"{entry.entry_id}_pd_tuning_profile"
        self._attr_device_info = _device_info(entry)

    @property
    def current_option(self) -> str:
        return self._entry.options.get(CONF_PD_TUNING_PROFILE, DEFAULT_PD_TUNING_PROFILE)

    async def async_select_option(self, option: str) -> None:
        new_options = {**self._entry.options, CONF_PD_TUNING_PROFILE: option}
        if option != PD_PROFILE_CUSTOM:
            preset = PD_TUNING_PROFILES[option]
            # Clear any per-parameter overrides so the preset applies cleanly,
            # and apply immediately (entry update listener also hot-reloads this).
            for key in (CONF_PD_KP, CONF_PD_KD, CONF_PD_MAX_POWER_CHANGE):
                new_options.pop(key, None)
            self._controller.pd.kp = preset[CONF_PD_KP]
            self._controller.pd.kd = preset[CONF_PD_KD]
            self._controller.pd.max_power_change_w = preset[CONF_PD_MAX_POWER_CHANGE]
        self.hass.config_entries.async_update_entry(self._entry, options=new_options)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    controller: EnergyManagerController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([PDTuningProfileSelect(controller, entry)])
