"""Tests for pricing.py's reactive percentile tracker and forecast parsing."""
from datetime import datetime, timedelta, timezone

from homeassistant.util import dt as dt_util

from custom_components.anker_solix_energy_manager.pricing import PriceTracker
from custom_components.anker_solix_energy_manager.const import PRICE_MIN_SAMPLES_BEFORE_ACTIVE


class _FakeState:
    def __init__(self, state, attributes=None):
        self.state = state
        self.attributes = attributes or {}


class _FakeStates:
    def __init__(self, data):
        self._data = data

    def get(self, entity_id):
        return self._data.get(entity_id)


class _FakeHass:
    def __init__(self, data):
        self.states = _FakeStates(data)


def test_current_price_reads_sensor():
    hass = _FakeHass({"sensor.price": _FakeState("0.25")})
    pt = PriceTracker(hass=hass, price_sensor="sensor.price")
    assert pt.current_price == 0.25


def test_current_price_unavailable_returns_none():
    hass = _FakeHass({"sensor.price": _FakeState("unavailable")})
    pt = PriceTracker(hass=hass, price_sensor="sensor.price")
    assert pt.current_price is None


def test_insufficient_history_is_conservative():
    hass = _FakeHass({"sensor.price": _FakeState("0.05")})
    pt = PriceTracker(hass=hass, price_sensor="sensor.price")
    for _ in range(PRICE_MIN_SAMPLES_BEFORE_ACTIVE - 1):
        pt.record_sample()
    assert pt.has_sufficient_history is False
    assert pt.is_cheap_now() is False  # conservative: never charges on noise


def test_is_cheap_now_after_sufficient_history():
    hass = _FakeHass({"sensor.price": _FakeState("0.10")})
    pt = PriceTracker(hass=hass, price_sensor="sensor.price", cheap_percentile=50)
    # Build a spread of samples: half cheap, half expensive.
    prices = [0.05] * 15 + [0.50] * 15
    for p in prices:
        hass.states._data["sensor.price"] = _FakeState(str(p))
        pt.record_sample()
    hass.states._data["sensor.price"] = _FakeState("0.05")
    assert pt.has_sufficient_history is True
    assert pt.is_cheap_now() is True

    hass.states._data["sensor.price"] = _FakeState("0.50")
    assert pt.is_cheap_now() is False


def test_prunes_samples_older_than_24h():
    hass = _FakeHass({"sensor.price": _FakeState("0.10")})
    pt = PriceTracker(hass=hass, price_sensor="sensor.price")
    old_ts = dt_util.utcnow().timestamp() - 25 * 3600
    pt._samples = [(old_ts, 0.99)]
    pt.record_sample()
    assert all(ts >= dt_util.utcnow().timestamp() - 24 * 3600 for ts, _ in pt._samples)
    assert 0.99 not in [p for _, p in pt._samples]


def test_forecast_hours_missing_attribute_returns_none():
    hass = _FakeHass({"sensor.price": _FakeState("0.10", attributes={})})
    pt = PriceTracker(hass=hass, price_sensor="sensor.price", forecast_attribute="forecast")
    assert pt.forecast_hours() is None


def test_forecast_hours_no_configured_attribute_returns_none():
    hass = _FakeHass({"sensor.price": _FakeState("0.10")})
    pt = PriceTracker(hass=hass, price_sensor="sensor.price", forecast_attribute=None)
    assert pt.forecast_hours() is None


def test_forecast_hours_parses_valid_entries():
    start = datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc)
    raw = [
        {"start": start.isoformat(), "price": 0.20},
        {"from": (start + timedelta(hours=1)).isoformat(), "total": 0.30},
    ]
    hass = _FakeHass({"sensor.price": _FakeState("0.10", attributes={"forecast": raw})})
    pt = PriceTracker(hass=hass, price_sensor="sensor.price", forecast_attribute="forecast")
    parsed = pt.forecast_hours()
    assert parsed is not None
    assert len(parsed) == 2
    assert parsed[0][1] == 0.20
    assert parsed[1][1] == 0.30


def test_forecast_hours_skips_unparseable_entries():
    raw = [{"nonsense": True}, {"start": "not-a-date", "price": 0.20}, "not-a-dict"]
    hass = _FakeHass({"sensor.price": _FakeState("0.10", attributes={"forecast": raw})})
    pt = PriceTracker(hass=hass, price_sensor="sensor.price", forecast_attribute="forecast")
    assert pt.forecast_hours() is None  # nothing parseable -> None, not an empty crash


def test_forecast_hours_non_list_attribute_returns_none():
    hass = _FakeHass({"sensor.price": _FakeState("0.10", attributes={"forecast": "not-a-list"})})
    pt = PriceTracker(hass=hass, price_sensor="sensor.price", forecast_attribute="forecast")
    assert pt.forecast_hours() is None
