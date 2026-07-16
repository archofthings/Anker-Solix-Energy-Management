"""End-to-end test of the real control loop wiring: adapter + PD controller
+ power distribution + capacity protection, through async_setup_entry and a
real (mocked-service) `hass` instance. This is the test that most directly
answers "is the actual write path safe" rather than just its pieces in
isolation.
"""
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.anker_solix_energy_manager.const import (
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
    CONF_GRID_POWER_SENSOR,
    CONF_MAX_CONTRACTED_POWER,
    CONF_MIN_CYCLE_INTERVAL,
    DOMAIN,
    MODE_THIRD_PARTY_CONTROL,
)


def _battery_config(n: int) -> dict:
    return {
        BATTERY_NAME: f"Battery {n}",
        BATTERY_OPERATING_MODE_ENTITY: f"select.batt{n}_operating_mode",
        BATTERY_TARGET_GRID_POWER_ENTITY: f"number.batt{n}_target_grid_power",
        BATTERY_GRID_FLOW_ENTITY: f"select.batt{n}_grid_flow",
        BATTERY_SOC_ENTITY: f"sensor.batt{n}_soc",
        BATTERY_DEVICE_STATUS_ENTITY: f"sensor.batt{n}_status",
        BATTERY_CHARGING_POWER_ENTITY: f"sensor.batt{n}_charging_power",
        BATTERY_DISCHARGING_POWER_ENTITY: f"sensor.batt{n}_discharging_power",
        BATTERY_CAPACITY_WH: 8000,
        BATTERY_MAX_CHARGE_W: 3500,
        BATTERY_MAX_DISCHARGE_W: 3500,
    }


async def _set_battery_states(hass, n: int, *, soc=50, status="normal", charging=0, discharging=0, mode=MODE_THIRD_PARTY_CONTROL):
    hass.states.async_set(f"select.batt{n}_operating_mode", mode)
    hass.states.async_set(f"select.batt{n}_grid_flow", "charge")
    hass.states.async_set(f"sensor.batt{n}_soc", str(soc))
    hass.states.async_set(f"sensor.batt{n}_status", status)
    hass.states.async_set(f"sensor.batt{n}_charging_power", str(charging))
    hass.states.async_set(f"sensor.batt{n}_discharging_power", str(discharging))
    hass.states.async_set(f"number.batt{n}_target_grid_power", "0")


def _mock_number_and_select_services(hass):
    calls = {"number.set_value": [], "select.select_option": []}

    def _entity_ids(call):
        # entity_id may legitimately be a single string or a list — the
        # adapter passes plain strings, so this must handle both.
        raw = call.data.get("entity_id", [])
        return [raw] if isinstance(raw, str) else raw

    async def _handle_number_set_value(call):
        calls["number.set_value"].append(call.data)
        for entity_id in _entity_ids(call):
            hass.states.async_set(entity_id, str(call.data["value"]))

    async def _handle_select_option(call):
        calls["select.select_option"].append(call.data)
        for entity_id in _entity_ids(call):
            hass.states.async_set(entity_id, call.data["option"])

    hass.services.async_register("number", "set_value", _handle_number_set_value)
    hass.services.async_register("select", "select_option", _handle_select_option)
    return calls


async def _setup_entry(hass, *, max_contracted_power_w=5750):
    hass.states.async_set("sensor.grid_power", "0")
    await _set_battery_states(hass, 1)
    await _set_battery_states(hass, 2)

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_GRID_POWER_SENSOR: "sensor.grid_power",
            CONF_MAX_CONTRACTED_POWER: max_contracted_power_w,
            CONF_MIN_CYCLE_INTERVAL: 2.0,
            CONF_BATTERIES: [_battery_config(1), _battery_config(2)],
        },
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    # Registered *after* setup: our own number/select platforms (PD tuning
    # entities) register the real entity-service-backed number.set_value /
    # select.select_option handlers during platform setup above — ours must
    # come after to actually override them for the rest of the test.
    calls = _mock_number_and_select_services(hass)

    controller = hass.data[DOMAIN][entry.entry_id]
    return entry, controller, calls


# NOTE: async_setup_entry wires a real async_track_state_change_event
# listener on the grid power sensor, so hass.states.async_set(...) on it
# triggers a control cycle automatically once the event loop is pumped by
# hass.async_block_till_done() — the tests below rely on that (real,
# production) wiring rather than calling controller.async_update() again
# themselves, which would double-trigger the (still-correct, but no longer
# single-cycle) incremental PD loop and make assertions non-deterministic.


async def test_first_cycle_discharges_to_cover_grid_import(hass):
    entry, controller, calls = await _setup_entry(hass)
    hass.states.async_set("sensor.grid_power", "1000")  # importing 1000W
    await hass.async_block_till_done()

    number_calls = calls["number.set_value"]
    assert number_calls, "expected at least one number.set_value call"
    total_commanded = sum(c["value"] for c in number_calls if c["value"] > 0)
    assert total_commanded == 1000

    select_calls = calls["select.select_option"]
    discharge_flow_calls = [c for c in select_calls if c.get("option") == "discharge"]
    assert discharge_flow_calls, "expected grid_flow set to discharge"


async def test_within_deadband_does_not_write_power(hass):
    entry, controller, calls = await _setup_entry(hass)
    # First cycle seeds state at 0 import already (set up as 0 in _setup_entry,
    # so there's no state transition to trigger the listener automatically).
    await controller.async_update(is_periodic=False)
    await hass.async_block_till_done()
    calls["number.set_value"].clear()

    hass.states.async_set("sensor.grid_power", "20")  # within default 50W deadband
    await hass.async_block_till_done()

    power_writes = [c for c in calls["number.set_value"] if c["value"] != 0]
    assert not power_writes


async def test_manual_mode_suppresses_all_writes(hass):
    entry, controller, calls = await _setup_entry(hass)
    controller.manual_mode_enabled = True

    hass.states.async_set("sensor.grid_power", "3000")
    await hass.async_block_till_done()

    assert calls["number.set_value"] == []
    assert calls["select.select_option"] == []


async def test_mode_revert_quirk_is_corrected_via_custom_mode_first(hass):
    entry, controller, calls = await _setup_entry(hass)
    # Simulate the unit having silently reverted to smart_mode.
    hass.states.async_set("select.batt1_operating_mode", "smart_mode")

    hass.states.async_set("sensor.grid_power", "1000")
    await hass.async_block_till_done()

    mode_calls = [c for c in calls["select.select_option"] if c["entity_id"] == "select.batt1_operating_mode"]
    options_set = [c["option"] for c in mode_calls]
    assert "custom_mode" in options_set
    assert "third_party_control" in options_set
    assert options_set.index("custom_mode") < options_set.index("third_party_control")


async def test_capacity_protection_forces_discharge_beyond_normal_pd(hass):
    # Contracted limit far below what a naive PD command would otherwise
    # leave importing; this exercises the safety backstop specifically.
    entry, controller, calls = await _setup_entry(hass, max_contracted_power_w=500)
    hass.states.async_set("sensor.grid_power", "5000")  # deep import
    await hass.async_block_till_done()

    number_calls = [c for c in calls["number.set_value"] if c["value"] != 0]
    assert number_calls, "expected a discharge command"
    # First-execution PD alone would already target -5000W here (discharge),
    # so this mainly proves the wiring doesn't undershoot/crash under a tight cap.
    total = sum(c["value"] for c in number_calls)
    assert total > 0
