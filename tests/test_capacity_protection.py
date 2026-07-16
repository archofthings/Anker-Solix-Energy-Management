"""Pure-function tests for capacity_protection.py."""
from custom_components.anker_solix_energy_manager.capacity_protection import (
    apply_capacity_protection,
)


def test_no_change_when_under_limit():
    result = apply_capacity_protection(
        0, raw_grid_w=2000, previous_power_w=0, max_contracted_power_w=5750, max_discharge_capacity_w=7000
    )
    assert result == 0


def test_charging_allowed_when_still_under_limit():
    result = apply_capacity_protection(
        1500, raw_grid_w=1000, previous_power_w=500, max_contracted_power_w=5750, max_discharge_capacity_w=7000
    )
    assert result == 1500


def test_charging_clamped_when_it_would_exceed_limit():
    result = apply_capacity_protection(
        2000, raw_grid_w=5000, previous_power_w=0, max_contracted_power_w=5750, max_discharge_capacity_w=7000
    )
    assert result == 750


def test_forces_discharge_when_house_load_alone_exceeds_limit():
    result = apply_capacity_protection(
        0, raw_grid_w=6000, previous_power_w=0, max_contracted_power_w=5750, max_discharge_capacity_w=7000
    )
    assert result == -250


def test_discharge_capacity_limits_the_forced_override():
    result = apply_capacity_protection(
        0, raw_grid_w=10000, previous_power_w=0, max_contracted_power_w=5750, max_discharge_capacity_w=3000
    )
    assert result == -3000


def test_existing_discharge_command_increased_not_reduced():
    result = apply_capacity_protection(
        -1000, raw_grid_w=4000, previous_power_w=-1000, max_contracted_power_w=5750, max_discharge_capacity_w=7000
    )
    assert result == -1000


def test_disabled_when_limit_is_zero_or_negative():
    result = apply_capacity_protection(
        5000, raw_grid_w=0, previous_power_w=0, max_contracted_power_w=0, max_discharge_capacity_w=7000
    )
    assert result == 5000


def test_never_increases_a_charge_command():
    result = apply_capacity_protection(
        500, raw_grid_w=100, previous_power_w=0, max_contracted_power_w=5750, max_discharge_capacity_w=7000
    )
    assert result <= 500
