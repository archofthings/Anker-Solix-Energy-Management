"""Tests for consumption_tracker.py. Uses the real `hass` fixture since
Store (persistence) needs a real HA instance for path resolution."""
from datetime import date

import pytest

from custom_components.anker_solix_energy_manager.consumption_tracker import ConsumptionTracker
from custom_components.anker_solix_energy_manager.const import DEFAULT_FALLBACK_DAILY_CONSUMPTION_KWH


def _set_state(hass, entity_id, value):
    hass.states.async_set(entity_id, str(value))


async def test_home_power_derived_from_energy_balance(hass):
    _set_state(hass, "sensor.solar", 2000)
    _set_state(hass, "sensor.grid", 500)
    tracker = ConsumptionTracker(hass=hass, entry_id="test", solar_power_sensor="sensor.solar", grid_power_sensor="sensor.grid")
    # solar 2000 + grid 500 - battery_net 1000 (charging) = 1500W home consumption
    assert tracker._current_home_power_w(battery_net_charge_w=1000) == 1500


async def test_home_power_clamped_at_zero_not_negative(hass):
    _set_state(hass, "sensor.solar", 100)
    _set_state(hass, "sensor.grid", -50)
    tracker = ConsumptionTracker(hass=hass, entry_id="test", solar_power_sensor="sensor.solar", grid_power_sensor="sensor.grid")
    # 100 + (-50) - 1000 = deeply negative -> clamped to 0
    assert tracker._current_home_power_w(battery_net_charge_w=1000) == 0.0


async def test_home_power_none_when_grid_sensor_missing(hass):
    tracker = ConsumptionTracker(hass=hass, entry_id="test", solar_power_sensor=None, grid_power_sensor="sensor.missing")
    assert tracker._current_home_power_w(battery_net_charge_w=0) is None


async def test_accumulate_integrates_energy_over_elapsed_time(hass):
    # Realistic cadence: control cycles run every few seconds, not hourly —
    # a single accumulate() call spanning a full hour would only happen after
    # an implausible gap, which is exactly what the 300s cap (tested below)
    # guards against. Simulate 360 cycles of 10s each (an hour of real
    # operation) to test the integration math itself.
    _set_state(hass, "sensor.solar", 0)
    _set_state(hass, "sensor.grid", 3600)  # steady 3600W -> 3.6kWh over 1h
    tracker = ConsumptionTracker(hass=hass, entry_id="test", solar_power_sensor="sensor.solar", grid_power_sensor="sensor.grid")
    tracker.today_date = date.today()
    for _ in range(360):
        tracker.accumulate(elapsed_s=10.0, battery_net_charge_w=0)
    assert tracker.today_kwh == pytest.approx(3.6, abs=0.01)


async def test_accumulate_caps_implausible_elapsed_time(hass):
    _set_state(hass, "sensor.solar", 0)
    _set_state(hass, "sensor.grid", 1000)
    tracker = ConsumptionTracker(hass=hass, entry_id="test", solar_power_sensor="sensor.solar", grid_power_sensor="sensor.grid")
    tracker.today_date = date.today()
    # A stale/huge elapsed_s (e.g. after HA restart) must not inject a huge jump.
    tracker.accumulate(elapsed_s=6 * 3600, battery_net_charge_w=0)
    # Capped at 300s -> 1000W * (300/3600)h = 0.0833 kWh
    assert tracker.today_kwh == pytest.approx(0.0833, abs=0.001)


async def test_accumulate_rolls_over_to_history_on_new_day(hass):
    _set_state(hass, "sensor.solar", 0)
    _set_state(hass, "sensor.grid", 0)
    tracker = ConsumptionTracker(hass=hass, entry_id="test", solar_power_sensor="sensor.solar", grid_power_sensor="sensor.grid")
    tracker.today_date = date(2020, 1, 1)
    tracker.today_wh = 15000.0  # 15 kWh accumulated "yesterday"
    tracker.accumulate(elapsed_s=None, battery_net_charge_w=0)
    assert list(tracker.history_kwh) == [15.0]
    assert tracker.today_wh == 0.0
    assert tracker.today_date == date.today()


async def test_average_daily_kwh_falls_back_when_no_history(hass):
    tracker = ConsumptionTracker(hass=hass, entry_id="test", solar_power_sensor=None, grid_power_sensor="sensor.grid")
    assert tracker.average_daily_kwh == DEFAULT_FALLBACK_DAILY_CONSUMPTION_KWH


async def test_average_daily_kwh_uses_history(hass):
    tracker = ConsumptionTracker(hass=hass, entry_id="test", solar_power_sensor=None, grid_power_sensor="sensor.grid")
    tracker.history_kwh.extend([10.0, 20.0])
    assert tracker.average_daily_kwh == 15.0


async def test_expected_consumption_over_scales_with_hours(hass):
    tracker = ConsumptionTracker(hass=hass, entry_id="test", solar_power_sensor=None, grid_power_sensor="sensor.grid")
    tracker.history_kwh.append(24.0)  # 1 kWh/hour average
    assert tracker.expected_consumption_over(8) == pytest.approx(8.0)
    assert tracker.expected_consumption_over(0) == 0.0


async def test_save_load_round_trip(hass):
    tracker = ConsumptionTracker(hass=hass, entry_id="roundtrip", solar_power_sensor=None, grid_power_sensor="sensor.grid")
    tracker.history_kwh.extend([5.0, 6.0])
    tracker.today_wh = 1234.0
    tracker.today_date = date(2026, 7, 1)
    tracker._dirty = True
    await tracker.async_save(force=True)

    tracker2 = ConsumptionTracker(hass=hass, entry_id="roundtrip", solar_power_sensor=None, grid_power_sensor="sensor.grid")
    await tracker2.async_load()
    assert list(tracker2.history_kwh) == [5.0, 6.0]
    assert tracker2.today_wh == 1234.0
    assert tracker2.today_date == date(2026, 7, 1)


async def test_save_is_debounced_without_force(hass):
    tracker = ConsumptionTracker(hass=hass, entry_id="debounce", solar_power_sensor=None, grid_power_sensor="sensor.grid")
    tracker._dirty = True
    tracker._last_save_monotonic = __import__("time").monotonic()  # pretend we just saved
    await tracker.async_save()  # should be a no-op, too soon
    assert tracker._dirty is True  # still dirty, since it didn't actually save
