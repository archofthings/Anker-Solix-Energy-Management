"""Manual mode: pauses the automatic PD control loop entirely.

While on, the integration will not write any setpoints or touch the
operating_mode entities — whatever mode/power you've set on the batteries
directly (e.g. via the Anker app) is left alone. Mirrors the reference
project's manual-mode switch, which exists specifically so you can safely
hand control back to the stock app without unloading the integration.
"""
from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EnergyManagerController
from .const import DOMAIN


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Anker Solix Energy Manager", manufacturer="archofthings")


class ManualModeSwitch(SwitchEntity):
    _attr_icon = "mdi:hand-back-right"

    def __init__(self, controller: EnergyManagerController, entry: ConfigEntry) -> None:
        self._controller = controller
        self._attr_name = "Manual Mode"
        self._attr_unique_id = f"{entry.entry_id}_manual_mode"
        self._attr_device_info = _device_info(entry)

    @property
    def is_on(self) -> bool:
        return self._controller.manual_mode_enabled

    async def async_turn_on(self, **kwargs) -> None:
        self._controller.manual_mode_enabled = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs) -> None:
        self._controller.manual_mode_enabled = False
        self._controller.pd.reset()  # re-seed on next cycle instead of resuming a stale command
        self.async_write_ha_state()


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    controller: EnergyManagerController = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([ManualModeSwitch(controller, entry)])
