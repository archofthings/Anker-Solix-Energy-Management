"""Tests for ev_exclusion.py."""
from custom_components.anker_solix_energy_manager.ev_exclusion import EVLoadExclusion


class _FakeState:
    def __init__(self, state):
        self.state = state


class _FakeStates:
    def __init__(self, data):
        self._data = data

    def get(self, entity_id):
        return self._data.get(entity_id)


class _FakeHass:
    def __init__(self, data):
        self.states = _FakeStates(data)


def test_no_sensors_configured_means_no_adjustment_and_no_block():
    hass = _FakeHass({})
    ev = EVLoadExclusion(hass=hass, charger_power_sensors=[], discharge_block_threshold_w=1000)
    assert ev.calculate_adjustment() == 0.0
    assert ev.discharge_blocked is False


def test_sums_multiple_chargers():
    hass = _FakeHass({"sensor.ev1": _FakeState("3000"), "sensor.ev2": _FakeState("2000")})
    ev = EVLoadExclusion(hass=hass, charger_power_sensors=["sensor.ev1", "sensor.ev2"], discharge_block_threshold_w=1000)
    assert ev.calculate_adjustment() == 5000.0


def test_missing_or_unavailable_sensor_treated_as_zero():
    hass = _FakeHass({"sensor.ev1": _FakeState("unavailable")})
    ev = EVLoadExclusion(hass=hass, charger_power_sensors=["sensor.ev1", "sensor.ev2"], discharge_block_threshold_w=1000)
    assert ev.calculate_adjustment() == 0.0


def test_negative_reading_clamped_to_zero():
    hass = _FakeHass({"sensor.ev1": _FakeState("-50")})
    ev = EVLoadExclusion(hass=hass, charger_power_sensors=["sensor.ev1"], discharge_block_threshold_w=1000)
    assert ev.total_ev_power_w() == 0.0


def test_discharge_blocked_above_threshold_only():
    hass = _FakeHass({"sensor.ev1": _FakeState("1500")})
    ev = EVLoadExclusion(hass=hass, charger_power_sensors=["sensor.ev1"], discharge_block_threshold_w=1000)
    assert ev.discharge_blocked is True

    hass2 = _FakeHass({"sensor.ev1": _FakeState("500")})
    ev2 = EVLoadExclusion(hass=hass2, charger_power_sensors=["sensor.ev1"], discharge_block_threshold_w=1000)
    assert ev2.discharge_blocked is False
