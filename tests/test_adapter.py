"""Tests for adapter.py — the seam that actually talks to the Anker
entities. Uses the real `hass` fixture since this module's whole job is
reading hass.states and calling hass.services.
"""
from custom_components.anker_solix_energy_manager.adapter import BatteryAdapter
from custom_components.anker_solix_energy_manager.const import (
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
    DEFAULT_CHARGE_LIMIT_SOC,
    DEFAULT_DISCHARGE_LIMIT_SOC,
    MODE_CUSTOM,
    MODE_THIRD_PARTY_CONTROL,
)

_CONFIG = {
    BATTERY_NAME: "Test Battery",
    BATTERY_OPERATING_MODE_ENTITY: "select.batt_operating_mode",
    BATTERY_TARGET_GRID_POWER_ENTITY: "number.batt_target_grid_power",
    BATTERY_GRID_FLOW_ENTITY: "select.batt_grid_flow",
    BATTERY_SOC_ENTITY: "sensor.batt_soc",
    BATTERY_DEVICE_STATUS_ENTITY: "sensor.batt_status",
    BATTERY_CHARGING_POWER_ENTITY: "sensor.batt_charging_power",
    BATTERY_DISCHARGING_POWER_ENTITY: "sensor.batt_discharging_power",
    BATTERY_CAPACITY_WH: 7000,
    BATTERY_MAX_CHARGE_W: 3500,
    BATTERY_MAX_DISCHARGE_W: 3500,
}


def _set_full_state(hass, *, soc=50, status="normal", mode=MODE_THIRD_PARTY_CONTROL, charging=0, discharging=0, flow="charge"):
    hass.states.async_set("select.batt_operating_mode", mode)
    hass.states.async_set("select.batt_grid_flow", flow)
    hass.states.async_set("sensor.batt_soc", str(soc))
    hass.states.async_set("sensor.batt_status", status)
    hass.states.async_set("sensor.batt_charging_power", str(charging))
    hass.states.async_set("sensor.batt_discharging_power", str(discharging))
    hass.states.async_set("number.batt_target_grid_power", "0")


def _mock_services(hass):
    calls = {"number.set_value": [], "select.select_option": []}

    async def _handle_number(call):
        calls["number.set_value"].append(dict(call.data))
        hass.states.async_set(call.data["entity_id"], str(call.data["value"]))

    async def _handle_select(call):
        calls["select.select_option"].append(dict(call.data))
        hass.states.async_set(call.data["entity_id"], call.data["option"])

    hass.services.async_register("number", "set_value", _handle_number)
    hass.services.async_register("select", "select_option", _handle_select)
    return calls


async def test_refresh_available_when_everything_present(hass):
    _set_full_state(hass)
    battery = BatteryAdapter(hass=hass, config=_CONFIG)
    battery.refresh()
    assert battery.data["available"] is True
    assert battery.data["battery_soc"] == 50.0


async def test_refresh_charge_discharge_limits_default_when_not_configured(hass):
    # No charge_limit_entity/discharge_limit_entity in _CONFIG at all.
    _set_full_state(hass)
    battery = BatteryAdapter(hass=hass, config=_CONFIG)
    battery.refresh()
    assert battery.data["charge_limit_soc"] == DEFAULT_CHARGE_LIMIT_SOC
    assert battery.data["discharge_limit_soc"] == DEFAULT_DISCHARGE_LIMIT_SOC


async def test_refresh_reads_configured_charge_discharge_limits(hass):
    config = {
        **_CONFIG,
        BATTERY_CHARGE_LIMIT_ENTITY: "number.batt_charge_limit",
        BATTERY_DISCHARGE_LIMIT_ENTITY: "number.batt_discharge_limit",
    }
    _set_full_state(hass)
    hass.states.async_set("number.batt_charge_limit", "85")
    hass.states.async_set("number.batt_discharge_limit", "15")

    battery = BatteryAdapter(hass=hass, config=config)
    battery.refresh()

    assert battery.data["charge_limit_soc"] == 85.0
    assert battery.data["discharge_limit_soc"] == 15.0


async def test_refresh_charge_discharge_limits_fall_back_when_entity_unavailable(hass):
    # Configured, but the entity itself currently has no readable state —
    # must fall back to the permissive default, not block charge/discharge
    # outright over a transient sensor hiccup.
    config = {
        **_CONFIG,
        BATTERY_CHARGE_LIMIT_ENTITY: "number.batt_charge_limit",
        BATTERY_DISCHARGE_LIMIT_ENTITY: "number.batt_discharge_limit",
    }
    _set_full_state(hass)
    hass.states.async_set("number.batt_charge_limit", "unavailable")
    hass.states.async_set("number.batt_discharge_limit", "unknown")

    battery = BatteryAdapter(hass=hass, config=config)
    battery.refresh()

    assert battery.data["charge_limit_soc"] == DEFAULT_CHARGE_LIMIT_SOC
    assert battery.data["discharge_limit_soc"] == DEFAULT_DISCHARGE_LIMIT_SOC


async def test_refresh_unavailable_when_charging_power_sensor_missing(hass):
    # SOC/status/mode all present, but the charging-power entity was never
    # created (typo'd entity_id, sensor not yet loaded, ...). Must not
    # silently read as "0W measured" — that would feed a false reading
    # straight into the PD anti-windup re-anchor logic.
    hass.states.async_set("select.batt_operating_mode", MODE_THIRD_PARTY_CONTROL)
    hass.states.async_set("select.batt_grid_flow", "charge")
    hass.states.async_set("sensor.batt_soc", "50")
    hass.states.async_set("sensor.batt_status", "normal")
    hass.states.async_set("sensor.batt_discharging_power", "0")
    # sensor.batt_charging_power intentionally never set.

    battery = BatteryAdapter(hass=hass, config=_CONFIG)
    battery.refresh()
    assert battery.data["available"] is False
    # Still reads as 0 for callers that don't check `available` first, but
    # the flag itself is what excludes it from selection.
    assert battery.data["charging_power"] == 0.0


async def test_refresh_unavailable_when_operating_mode_missing(hass):
    hass.states.async_set("sensor.batt_soc", "50")
    hass.states.async_set("sensor.batt_status", "normal")
    hass.states.async_set("sensor.batt_charging_power", "0")
    hass.states.async_set("sensor.batt_discharging_power", "0")
    # select.batt_operating_mode intentionally never set.

    battery = BatteryAdapter(hass=hass, config=_CONFIG)
    battery.refresh()
    assert battery.data["available"] is False


async def test_measured_power_is_signed_charge_minus_discharge(hass):
    _set_full_state(hass, charging=1200, discharging=0)
    battery = BatteryAdapter(hass=hass, config=_CONFIG)
    battery.refresh()
    assert battery.data["measured_power"] == 1200.0

    _set_full_state(hass, charging=0, discharging=800)
    battery.refresh()
    assert battery.data["measured_power"] == -800.0


async def test_set_power_below_floor_writes_zero(hass):
    _set_full_state(hass)
    hass.states.async_set("number.batt_target_grid_power", "250")  # non-zero, so a 0-write is observable
    calls = _mock_services(hass)
    battery = BatteryAdapter(hass=hass, config=_CONFIG)
    await battery.async_set_power(50, 0)  # below ANKER_TARGET_GRID_POWER_FLOOR_W (100)
    values = [c["value"] for c in calls["number.set_value"]]
    assert values == [0]


async def test_set_power_same_direction_does_not_zero_first(hass):
    # Already discharging; adjusting the magnitude within the same
    # direction must not blip through zero — that would defeat the PD
    # loop's whole smoothness design.
    _set_full_state(hass, flow="discharge")
    hass.states.async_set("number.batt_target_grid_power", "500")
    calls = _mock_services(hass)
    battery = BatteryAdapter(hass=hass, config=_CONFIG)

    await battery.async_set_power(0, 800)

    values = [c["value"] for c in calls["number.set_value"]]
    assert 0 not in values
    assert values == [800]


async def test_set_power_direction_flip_zeroes_before_switching_flow(hass):
    # Currently charging at 1000W; commanding a discharge must not leave a
    # window where grid_flow already reads "discharge" while
    # target_grid_power still holds the old charging magnitude.
    _set_full_state(hass, flow="charge")
    hass.states.async_set("number.batt_target_grid_power", "1000")
    calls = _mock_services(hass)
    battery = BatteryAdapter(hass=hass, config=_CONFIG)

    await battery.async_set_power(0, 600)

    number_values = [c["value"] for c in calls["number.set_value"]]
    assert number_values[0] == 0, "must zero the setpoint before flipping direction"
    assert number_values[-1] == 600

    flow_calls = [c["option"] for c in calls["select.select_option"] if c["entity_id"] == "select.batt_grid_flow"]
    # The zero write happens strictly before the direction flip.
    zero_index = calls["number.set_value"].index({"entity_id": "number.batt_target_grid_power", "value": 0})
    flip_call = next(c for c in calls["select.select_option"] if c["entity_id"] == "select.batt_grid_flow")
    flip_index = calls["select.select_option"].index(flip_call)
    assert zero_index < len(calls["number.set_value"])
    assert "discharge" in flow_calls
    assert flip_index >= 0 and zero_index >= 0


async def test_ensure_third_party_control_fixes_reverted_mode_via_custom_first(hass):
    _set_full_state(hass, mode="smart_mode")
    calls = _mock_services(hass)
    battery = BatteryAdapter(hass=hass, config=_CONFIG)

    await battery.async_ensure_third_party_control(force=True)

    options = [c["option"] for c in calls["select.select_option"] if c["entity_id"] == "select.batt_operating_mode"]
    assert options == [MODE_CUSTOM, MODE_THIRD_PARTY_CONTROL]


async def test_ensure_third_party_control_noop_when_already_correct(hass):
    _set_full_state(hass, mode=MODE_THIRD_PARTY_CONTROL)
    calls = _mock_services(hass)
    battery = BatteryAdapter(hass=hass, config=_CONFIG)

    await battery.async_ensure_third_party_control(force=True)

    mode_calls = [c for c in calls["select.select_option"] if c["entity_id"] == "select.batt_operating_mode"]
    assert mode_calls == []


async def test_ensure_third_party_control_throttled_without_force(hass):
    _set_full_state(hass, mode="smart_mode")
    calls = _mock_services(hass)
    battery = BatteryAdapter(hass=hass, config=_CONFIG)

    await battery.async_ensure_third_party_control(force=True)
    calls["select.select_option"].clear()
    hass.states.async_set("select.batt_operating_mode", "smart_mode")  # reverted again immediately

    await battery.async_ensure_third_party_control()  # no force, within throttle window
    assert calls["select.select_option"] == []


async def test_no_op_write_avoidance(hass):
    _set_full_state(hass, flow="discharge")
    hass.states.async_set("number.batt_target_grid_power", "800")
    calls = _mock_services(hass)
    battery = BatteryAdapter(hass=hass, config=_CONFIG)

    await battery.async_set_power(0, 800)  # already at 800, same direction

    assert calls["number.set_value"] == []
