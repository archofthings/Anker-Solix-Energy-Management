"""Diagnostic sensors for the PD controller and battery allocation."""
from __future__ import annotations

from homeassistant.components.sensor import SensorEntity, SensorStateClass
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import EnergyManagerController
from .const import DOMAIN


def _device_info(entry: ConfigEntry) -> DeviceInfo:
    return DeviceInfo(identifiers={(DOMAIN, entry.entry_id)}, name="Anker Solix Energy Manager", manufacturer="archofthings")


class _BaseSensor(SensorEntity):
    _attr_should_poll = True
    _attr_scan_interval = None

    def __init__(self, controller: EnergyManagerController, entry: ConfigEntry, key: str, name: str) -> None:
        self._controller = controller
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_name = name
        self._attr_device_info = _device_info(entry)


class CommandPowerSensor(_BaseSensor):
    _attr_native_unit_of_measurement = "W"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:transmission-tower"

    @property
    def native_value(self):
        return round(self._controller.last_command_w)


class GridPowerSensor(_BaseSensor):
    _attr_native_unit_of_measurement = "W"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:meter-electric"

    @property
    def native_value(self):
        v = self._controller.last_grid_power_w
        return round(v) if v is not None else None


class PDQualityRmsSensor(_BaseSensor):
    _attr_native_unit_of_measurement = "W"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:chart-bell-curve"

    @property
    def native_value(self):
        v = self._controller.pd.quality_rms_error_w
        return round(v, 1) if v is not None else None


class PDQualityOscillationSensor(_BaseSensor):
    _attr_native_unit_of_measurement = "events/min"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:sine-wave"

    @property
    def native_value(self):
        return round(self._controller.pd.quality_oscillation_per_min, 2)


class ActiveBatteriesSensor(_BaseSensor):
    _attr_icon = "mdi:battery-sync"

    @property
    def native_value(self):
        pd = self._controller.power_distribution
        if pd.active_charge_batteries:
            return ", ".join(b.name for b in pd.active_charge_batteries)
        if pd.active_discharge_batteries:
            return ", ".join(b.name for b in pd.active_discharge_batteries)
        return "idle"


class BatteryAllocationSensor(_BaseSensor):
    _attr_native_unit_of_measurement = "W"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:battery-charging-medium"

    def __init__(self, controller: EnergyManagerController, entry: ConfigEntry, battery_name: str) -> None:
        super().__init__(controller, entry, f"alloc_{battery_name}", f"{battery_name} Allocated Power")
        self._battery_name = battery_name

    @property
    def native_value(self):
        return round(self._controller.last_allocation_w.get(self._battery_name, 0))


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    controller: EnergyManagerController = hass.data[DOMAIN][entry.entry_id]
    entities = [
        CommandPowerSensor(controller, entry, "command_power", "Battery Command Power"),
        GridPowerSensor(controller, entry, "grid_power", "Grid Power (filtered input)"),
        PDQualityRmsSensor(controller, entry, "pd_rms_error", "PD Control Quality (RMS error)"),
        PDQualityOscillationSensor(controller, entry, "pd_oscillation", "PD Oscillation Rate"),
        ActiveBatteriesSensor(controller, entry, "active_batteries", "Active Batteries"),
    ]
    for b in controller.batteries:
        entities.append(BatteryAllocationSensor(controller, entry, b.name))
    async_add_entities(entities)
