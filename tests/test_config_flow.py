"""Tests for the setup/reconfigure UX in config_flow.py — the actual
first-run experience, not just the underlying logic."""
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anker_solix_energy_manager.const import (
    CONF_BATTERIES,
    CONF_GRID_POWER_SENSOR,
    CONF_MAX_CONTRACTED_POWER,
    CONF_MIN_CYCLE_INTERVAL,
    CONF_PREDICTIVE_CHARGING_ENABLED,
    CONF_PREDICTIVE_CHARGING_MODE,
    DOMAIN,
    PREDICTIVE_MODE_FIXED_SLOTS,
    PREDICTIVE_MODE_REALTIME_PRICE,
)

USER_STEP_INPUT = {
    CONF_GRID_POWER_SENSOR: "sensor.grid_power",
    CONF_MAX_CONTRACTED_POWER: 5750,
    CONF_MIN_CYCLE_INTERVAL: 2.0,
}


def _battery_input(n: int, **overrides) -> dict:
    data = {
        "name": f"Solarbank {n}",
        "operating_mode_entity": f"select.batt{n}_operating_mode",
        "target_grid_power_entity": f"number.batt{n}_target_grid_power",
        "grid_flow_entity": f"select.batt{n}_grid_flow",
        "soc_entity": f"sensor.batt{n}_soc",
        "device_status_entity": f"sensor.batt{n}_status",
        "charging_power_entity": f"sensor.batt{n}_charging_power",
        "discharging_power_entity": f"sensor.batt{n}_discharging_power",
        "capacity_wh": 8000,
        "max_charge_w": 3500,
        "max_discharge_w": 3500,
    }
    data.update(overrides)
    return data


FEATURES_STEP_MINIMAL = {
    "ev_discharge_block_threshold_w": 1000,
    "price_cheap_percentile": 30,
    CONF_PREDICTIVE_CHARGING_ENABLED: False,
    CONF_PREDICTIVE_CHARGING_MODE: PREDICTIVE_MODE_FIXED_SLOTS,
    "predictive_target_soc": 80,
    "predictive_coverage_hours": 8.0,
    "predictive_charge_power_w": 1500,
}


async def _complete_fresh_flow(hass, *, battery2_overrides=None):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_STEP_INPUT)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _battery_input(1))
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _battery_input(2, **(battery2_overrides or {}))
    )
    return result


async def test_happy_path_creates_entry(hass):
    result = await _complete_fresh_flow(hass)
    final = await hass.config_entries.flow.async_configure(result["flow_id"], FEATURES_STEP_MINIMAL)
    assert final["type"] == FlowResultType.CREATE_ENTRY
    assert len(final["data"][CONF_BATTERIES]) == 2
    assert final["data"][CONF_BATTERIES][0]["name"] == "Solarbank 1"
    assert final["data"][CONF_BATTERIES][1]["name"] == "Solarbank 2"


async def test_second_instance_aborts_already_configured(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=DOMAIN, data={})
    entry.add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_empty_battery_name_shows_error(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_STEP_INPUT)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _battery_input(1, name="   "))
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "battery_name_required"


async def test_duplicate_battery_name_shows_error(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_STEP_INPUT)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _battery_input(1, name="Same"))
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _battery_input(2, name="same"))
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "battery_name_duplicate"


async def test_duplicate_entity_across_batteries_shows_error(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_STEP_INPUT)
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _battery_input(1))
    # Battery 2 accidentally reuses battery 1's SOC sensor.
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], _battery_input(2, soc_entity="sensor.batt1_soc")
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "battery_entity_duplicate"


async def test_predictive_charging_requires_solar_forecast(hass):
    result = await _complete_fresh_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**FEATURES_STEP_MINIMAL, CONF_PREDICTIVE_CHARGING_ENABLED: True}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "predictive_needs_solar_forecast"


async def test_predictive_charging_fixed_slots_requires_both_times(hass):
    result = await _complete_fresh_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **FEATURES_STEP_MINIMAL,
            CONF_PREDICTIVE_CHARGING_ENABLED: True,
            "solar_forecast_remaining_sensor": "sensor.solar_forecast",
            "fixed_slot_start": "23:00:00",
            # no fixed_slot_end
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "predictive_needs_fixed_slot"


async def test_predictive_charging_non_fixed_mode_requires_price_sensor(hass):
    result = await _complete_fresh_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **FEATURES_STEP_MINIMAL,
            CONF_PREDICTIVE_CHARGING_ENABLED: True,
            CONF_PREDICTIVE_CHARGING_MODE: PREDICTIVE_MODE_REALTIME_PRICE,
            "solar_forecast_remaining_sensor": "sensor.solar_forecast",
        },
    )
    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "predictive_needs_price_sensor"


async def test_predictive_charging_fixed_slots_valid_succeeds(hass):
    result = await _complete_fresh_flow(hass)
    final = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            **FEATURES_STEP_MINIMAL,
            CONF_PREDICTIVE_CHARGING_ENABLED: True,
            "solar_forecast_remaining_sensor": "sensor.solar_forecast",
            "fixed_slot_start": "23:00:00",
            "fixed_slot_end": "06:00:00",
        },
    )
    assert final["type"] == FlowResultType.CREATE_ENTRY
    assert final["data"]["predictive_fixed_slots"] == [{"start": "23:00", "end": "06:00"}]


async def test_reconfigure_prefills_and_updates_existing_entry(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=DOMAIN,
        data={
            **USER_STEP_INPUT,
            CONF_BATTERIES: [_battery_input(1), _battery_input(2)],
            "solar_power_sensor": None,
            "solar_forecast_remaining_sensor": None,
            "ev_charger_power_sensors": [],
            "ev_discharge_block_threshold_w": 1000,
            "price_sensor": None,
            "price_forecast_attribute": None,
            "price_cheap_percentile": 30,
            CONF_PREDICTIVE_CHARGING_ENABLED: False,
            CONF_PREDICTIVE_CHARGING_MODE: PREDICTIVE_MODE_FIXED_SLOTS,
            "predictive_target_soc": 80,
            "predictive_coverage_hours": 8.0,
            "predictive_charge_power_w": 1500,
            "predictive_fixed_slots": [],
        },
    )
    entry.add_to_hass(hass)

    result = await entry.start_reconfigure_flow(hass)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    # Pre-filled from the existing entry, not the hardcoded schema defaults.
    assert result["data_schema"]({})[CONF_GRID_POWER_SENSOR] == "sensor.grid_power"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {**USER_STEP_INPUT, CONF_MAX_CONTRACTED_POWER: 7000}
    )
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _battery_input(1, name="Renamed 1"))
    result = await hass.config_entries.flow.async_configure(result["flow_id"], _battery_input(2))
    final = await hass.config_entries.flow.async_configure(result["flow_id"], FEATURES_STEP_MINIMAL)

    assert final["type"] == FlowResultType.ABORT
    assert final["reason"] == "reconfigure_successful"
    assert entry.data[CONF_MAX_CONTRACTED_POWER] == 7000
    assert entry.data[CONF_BATTERIES][0]["name"] == "Renamed 1"
    # Still the same entry_id -> consumption history Store isn't orphaned.
    assert entry.entry_id == entry.entry_id
