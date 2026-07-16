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


class ConsumptionTodaySensor(_BaseSensor):
    _attr_native_unit_of_measurement = "kWh"
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_icon = "mdi:home-lightning-bolt"

    @property
    def native_value(self):
        return self._controller.consumption_tracker.today_kwh


class ConsumptionAverageDailySensor(_BaseSensor):
    _attr_native_unit_of_measurement = "kWh"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_icon = "mdi:chart-line"

    @property
    def native_value(self):
        return round(self._controller.consumption_tracker.average_daily_kwh, 2)


class PredictiveChargingStatusSensor(_BaseSensor):
    _attr_icon = "mdi:transmission-tower-import"

    @property
    def native_value(self):
        return "charging" if self._controller.predictive_charging_active else "idle"

    @property
    def extra_state_attributes(self):
        return {"reason": self._controller.predictive_charging_reason}


class EVChargingPowerSensor(_BaseSensor):
    _attr_native_unit_of_measurement = "W"
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:ev-station"

    @property
    def native_value(self):
        return round(self._controller.ev_exclusion.total_ev_power_w())


class CurrentPriceSensor(_BaseSensor):
    _attr_state_class = SensorStateClass.MEASUREMENT
    _attr_icon = "mdi:currency-eur"

    @property
    def native_value(self):
        return self._controller.price_tracker.current_price

    @property
    def extra_state_attributes(self):
        return {"is_cheap_now": self._controller.price_tracker.is_cheap_now()}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback) -> None:
    controller: EnergyManagerController = hass.data[DOMAIN][entry.entry_id]
    entities = [
        CommandPowerSensor(controller, entry, "command_power", "Battery Command Power"),
        GridPowerSensor(controller, entry, "grid_power", "Grid Power (filtered input)"),
        PDQualityRmsSensor(controller, entry, "pd_rms_error", "PD Control Quality (RMS error)"),
        PDQualityOscillationSensor(controller, entry, "pd_oscillation", "PD Oscillation Rate"),
        ActiveBatteriesSensor(controller, entry, "active_batteries", "Active Batteries"),
        ConsumptionTodaySensor(controller, entry, "consumption_today", "Consumption Today"),
        ConsumptionAverageDailySensor(controller, entry, "consumption_avg_daily", "Average Daily Consumption"),
    ]
    for b in controller.batteries:
        entities.append(BatteryAllocationSensor(controller, entry, b.name))
    if controller.predictive_charging is not None:
        entities.append(PredictiveChargingStatusSensor(controller, entry, "predictive_charging_status", "Predictive Charging Status"))
    if controller.ev_exclusion is not None:
        entities.append(EVChargingPowerSensor(controller, entry, "ev_charging_power", "EV Charging Power"))
    if controller.price_tracker is not None:
        entities.append(CurrentPriceSensor(controller, entry, "current_price", "Current Electricity Price"))
    async_add_entities(entities)
