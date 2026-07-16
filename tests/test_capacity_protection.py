"""Pure-function tests for capacity_protection.py — no HA dependency.

Loaded via importlib directly from the file path (not as
`custom_components.anker_solix_energy_manager.capacity_protection`) so
importing this pure module doesn't trigger the package's __init__.py, which
pulls in `homeassistant` — not installed/needed for these tests.
"""
import importlib.util
import os

_MODULE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "custom_components", "anker_solix_energy_manager", "capacity_protection.py"
)
_spec = importlib.util.spec_from_file_location("capacity_protection", _MODULE_PATH)
_capacity_protection = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_capacity_protection)
apply_capacity_protection = _capacity_protection.apply_capacity_protection


def test_no_change_when_under_limit():
    # House load 2000W, no battery activity, well under a 5750W limit.
    result = apply_capacity_protection(
        0, raw_grid_w=2000, previous_power_w=0, max_contracted_power_w=5750, max_discharge_capacity_w=7000
    )
    assert result == 0


def test_charging_allowed_when_still_under_limit():
    # House load implied: raw_grid_w=1000 with battery already charging 500W
    # -> house_load = 1000 - 500 = 500. New charge command of 1500W ->
    # projected = 500 + 1500 = 2000W, under a 5750W limit.
    result = apply_capacity_protection(
        1500, raw_grid_w=1000, previous_power_w=500, max_contracted_power_w=5750, max_discharge_capacity_w=7000
    )
    assert result == 1500


def test_charging_clamped_when_it_would_exceed_limit():
    # House load 5000W (raw_grid_w=5000, previous_power_w=0). Requesting to
    # charge 2000W would push projected import to 7000W, over a 5750W limit.
    # Expect the charge command reduced so projected import == limit.
    result = apply_capacity_protection(
        2000, raw_grid_w=5000, previous_power_w=0, max_contracted_power_w=5750, max_discharge_capacity_w=7000
    )
    assert result == 750  # 5000 + 750 == 5750


def test_forces_discharge_when_house_load_alone_exceeds_limit():
    # House load 6000W already exceeds a 5750W limit even with battery idle.
    # Must force discharge, not just reduce charging to zero.
    result = apply_capacity_protection(
        0, raw_grid_w=6000, previous_power_w=0, max_contracted_power_w=5750, max_discharge_capacity_w=7000
    )
    assert result == -250  # 6000 - 250 == 5750


def test_discharge_capacity_limits_the_forced_override():
    # House load 10000W, limit 5750W -> overshoot 4250W, but only 3000W of
    # discharge capacity is available. Can't fully protect the breaker, but
    # must use all available discharge capacity trying.
    result = apply_capacity_protection(
        0, raw_grid_w=10000, previous_power_w=0, max_contracted_power_w=5750, max_discharge_capacity_w=3000
    )
    assert result == -3000


def test_existing_discharge_command_increased_not_reduced():
    # Already discharging 1000W (previous_power_w=-1000, raw_grid_w reflects
    # that). House load implied = raw_grid_w - previous_power_w = 4000 - (-1000) = 5000.
    # Candidate command of -1000 (continue as-is) -> projected = 5000 - 1000 = 4000, fine.
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
    # Sanity: this function only ever reduces power_w toward/into discharge,
    # never increases a charge command above what was requested.
    result = apply_capacity_protection(
        500, raw_grid_w=100, previous_power_w=0, max_contracted_power_w=5750, max_discharge_capacity_w=7000
    )
    assert result <= 500


if __name__ == "__main__":
    import inspect
    failures = 0
    tests = {name: fn for name, fn in globals().items() if name.startswith("test_")}
    for name, fn in tests.items():
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {name}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    raise SystemExit(1 if failures else 0)
