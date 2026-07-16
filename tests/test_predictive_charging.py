"""Tests for predictive_charging.py — coverage gating, mode dispatch, and
the anti-chatter dwell-time mechanism."""
from datetime import datetime, timedelta, timezone

from custom_components.anker_solix_energy_manager.const import (
    PREDICTIVE_MODE_DYNAMIC_PRICING,
    PREDICTIVE_MODE_FIXED_SLOTS,
    PREDICTIVE_MODE_REALTIME_PRICE,
)
from custom_components.anker_solix_energy_manager.predictive_charging import PredictiveChargingManager


class _FakeState:
    def __init__(self, state):
        self.state = state


class _FakeStates:
    def __init__(self, data):
        self._data = data

    def get(self, entity_id):
        return self._data.get(entity_id)


class _FakeHass:
    def __init__(self, data=None):
        self.states = _FakeStates(data or {})


class _FakeBattery:
    def __init__(self, name, soc, capacity_wh=8000, available=True):
        self.name = name
        self.capacity_wh = capacity_wh
        self.data = {"available": available, "battery_soc": soc}


class _FakeConsumptionTracker:
    def __init__(self, daily_kwh=24.0):
        self._daily_kwh = daily_kwh

    def expected_consumption_over(self, hours: float) -> float:
        return self._daily_kwh / 24.0 * hours


class _FakePriceTracker:
    def __init__(self, *, cheap_now=False, has_history=True, forecast=None, cheap_percentile=30):
        self._cheap_now = cheap_now
        self.has_sufficient_history = has_history
        self._forecast = forecast
        self.cheap_percentile = cheap_percentile

    def is_cheap_now(self):
        return self._cheap_now

    def forecast_hours(self):
        return self._forecast


def _make_manager(**overrides):
    kwargs = dict(
        hass=_FakeHass(),
        enabled=True,
        mode=PREDICTIVE_MODE_FIXED_SLOTS,
        target_soc=80,
        coverage_hours=8.0,
        charge_power_w=1500,
        fixed_slots=[],
        solar_forecast_remaining_sensor=None,
        consumption_tracker=_FakeConsumptionTracker(),
        price_tracker=None,
        batteries=[_FakeBattery("A", soc=50)],
    )
    kwargs.update(overrides)
    return PredictiveChargingManager(**kwargs)


def test_disabled_never_charges():
    mgr = _make_manager(enabled=False)
    result, reason = mgr.should_charge(now=datetime(2026, 1, 1, 3, 0))
    assert result is False
    assert reason == "disabled"


def test_no_shortfall_when_battery_already_at_target():
    mgr = _make_manager(batteries=[_FakeBattery("A", soc=90)], target_soc=80)
    result, _ = mgr.should_charge(now=datetime(2026, 1, 1, 3, 0))
    assert result is False


def test_no_shortfall_when_solar_and_battery_cover_it():
    # 8h coverage window, 24kWh/day average -> 8kWh expected. Battery alone
    # (8000Wh at 50% = 4kWh) + solar forecast covers the rest.
    mgr = _make_manager(
        batteries=[_FakeBattery("A", soc=50, capacity_wh=8000)],
        solar_forecast_remaining_sensor="sensor.solar_forecast",
        hass=_FakeHass({"sensor.solar_forecast": _FakeState("10")}),
    )
    assert mgr.coverage_shortfall_kwh() == 0.0
    result, reason = mgr.should_charge(now=datetime(2026, 1, 1, 3, 0))
    assert result is False
    assert "sufficient" in reason


def test_shortfall_present_without_solar_sensor_defaults_conservative():
    # No solar forecast sensor configured -> assumed 0kWh remaining solar,
    # so shortfall = expected - battery only.
    mgr = _make_manager(batteries=[_FakeBattery("A", soc=10, capacity_wh=8000)])
    # expected 8kWh, battery has 0.8kWh -> real shortfall
    assert mgr.coverage_shortfall_kwh() > 0


def test_fixed_slot_mode_requires_being_inside_the_slot():
    # Independent manager instances: should_charge's dwell-time gating (see
    # below) applies to a live sequence of calls on the *same* manager — two
    # unrelated point-in-time scenarios must not be run through one.
    kwargs = dict(
        mode=PREDICTIVE_MODE_FIXED_SLOTS,
        fixed_slots=[{"start": "23:00", "end": "06:00"}],
        batteries=[_FakeBattery("A", soc=10)],
    )
    inside = _make_manager(**kwargs).should_charge(now=datetime(2026, 1, 1, 2, 0))[0]
    outside = _make_manager(**kwargs).should_charge(now=datetime(2026, 1, 1, 12, 0))[0]
    assert inside is True
    assert outside is False


def test_fixed_slot_mode_with_no_slots_configured_never_charges():
    mgr = _make_manager(mode=PREDICTIVE_MODE_FIXED_SLOTS, fixed_slots=[], batteries=[_FakeBattery("A", soc=10)])
    result, reason = mgr.should_charge(now=datetime(2026, 1, 1, 3, 0))
    assert result is False


def test_realtime_price_mode_uses_reactive_cheap_check():
    mgr = _make_manager(
        mode=PREDICTIVE_MODE_REALTIME_PRICE,
        price_tracker=_FakePriceTracker(cheap_now=True, has_history=True),
        batteries=[_FakeBattery("A", soc=10)],
    )
    result, _ = mgr.should_charge(now=datetime(2026, 1, 1, 3, 0))
    assert result is True


def test_realtime_price_mode_waits_for_sufficient_history():
    mgr = _make_manager(
        mode=PREDICTIVE_MODE_REALTIME_PRICE,
        price_tracker=_FakePriceTracker(cheap_now=True, has_history=False),
        batteries=[_FakeBattery("A", soc=10)],
    )
    result, reason = mgr.should_charge(now=datetime(2026, 1, 1, 3, 0))
    assert result is False


def test_dynamic_pricing_mode_uses_forecast_lookahead():
    start = datetime(2026, 1, 1, 3, 0, tzinfo=timezone.utc)
    forecast = [(start, 0.05), (start + timedelta(hours=1), 0.50)]
    mgr = _make_manager(
        mode=PREDICTIVE_MODE_DYNAMIC_PRICING,
        price_tracker=_FakePriceTracker(forecast=forecast, cheap_percentile=60),
        batteries=[_FakeBattery("A", soc=10)],
    )
    result, _ = mgr.should_charge(now=start)
    assert result is True


def test_dynamic_pricing_mode_no_forecast_available_does_not_fall_back_to_reactive():
    mgr = _make_manager(
        mode=PREDICTIVE_MODE_DYNAMIC_PRICING,
        price_tracker=_FakePriceTracker(forecast=None, cheap_now=True),
        batteries=[_FakeBattery("A", soc=10)],
    )
    result, reason = mgr.should_charge(now=datetime(2026, 1, 1, 3, 0))
    assert result is False
    assert "forecast unavailable" in reason


def test_dwell_time_holds_state_against_rapid_flapping():
    mgr = _make_manager(
        mode=PREDICTIVE_MODE_REALTIME_PRICE,
        price_tracker=_FakePriceTracker(cheap_now=True, has_history=True),
        batteries=[_FakeBattery("A", soc=10)],
    )
    now = datetime(2026, 1, 1, 3, 0)
    first, _ = mgr.should_charge(now=now)
    assert first is True  # first transition always allowed (starts at -inf)

    # Flip the underlying gate to "not cheap" immediately after — without
    # dwell time this would flip off right away.
    mgr.price_tracker._cheap_now = False
    second, reason = mgr.should_charge(now=now)
    assert second is True  # held, dwell time hasn't elapsed
    assert "dwell time not elapsed" in reason


def test_dwell_time_releases_after_forcing_last_transition_in_the_past():
    mgr = _make_manager(
        mode=PREDICTIVE_MODE_REALTIME_PRICE,
        price_tracker=_FakePriceTracker(cheap_now=True, has_history=True),
        batteries=[_FakeBattery("A", soc=10)],
    )
    now = datetime(2026, 1, 1, 3, 0)
    assert mgr.should_charge(now=now)[0] is True

    mgr.price_tracker._cheap_now = False
    import time as _time
    mgr._last_transition_monotonic = _time.monotonic() - 10_000  # long ago
    result, reason = mgr.should_charge(now=now)
    assert result is False
    assert "dwell time not elapsed" not in reason


def test_disabling_bypasses_dwell_time_immediately():
    mgr = _make_manager(
        mode=PREDICTIVE_MODE_REALTIME_PRICE,
        price_tracker=_FakePriceTracker(cheap_now=True, has_history=True),
        batteries=[_FakeBattery("A", soc=10)],
    )
    now = datetime(2026, 1, 1, 3, 0)
    assert mgr.should_charge(now=now)[0] is True
    mgr.enabled = False
    result, reason = mgr.should_charge(now=now)
    assert result is False
    assert reason == "disabled"


def test_unavailable_batteries_excluded_from_target_check():
    mgr = _make_manager(batteries=[_FakeBattery("A", soc=10, available=False)])
    assert mgr._any_battery_below_target() is False
