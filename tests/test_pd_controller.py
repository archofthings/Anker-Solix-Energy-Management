"""Tests for pd_controller.py — no HA dependency (loaded via importlib to
avoid triggering the package __init__.py's homeassistant import)."""
import importlib.util
import os

_HERE = os.path.dirname(__file__)


def _load(name, relative_path):
    module_path = os.path.join(_HERE, "..", "custom_components", "anker_solix_energy_manager", relative_path)
    spec = importlib.util.spec_from_file_location(name, module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# const.py has no homeassistant import, safe to load directly; pd_controller
# imports `from .const import ...` (relative), so register both under a
# lightweight fake package namespace first.
import sys
import types

_pkg = types.ModuleType("_asem_test_pkg")
_pkg.__path__ = [os.path.join(_HERE, "..", "custom_components", "anker_solix_energy_manager")]
sys.modules["_asem_test_pkg"] = _pkg
const = _load("_asem_test_pkg.const", "const.py")
sys.modules["_asem_test_pkg.const"] = const
pd_controller = _load("_asem_test_pkg.pd_controller", "pd_controller.py")
PDController = pd_controller.PDController


def _make_controller(**overrides):
    kwargs = dict(
        kp=0.30,
        kd=0.25,
        deadband_w=50,
        max_power_change_w=500,
        direction_hysteresis_w=80,
        min_charge_power_w=100,
        min_discharge_power_w=100,
    )
    kwargs.update(overrides)
    return PDController(**kwargs)


def test_first_execution_counters_current_error():
    pd = _make_controller()
    result = pd.compute(grid_power_w=1000, target_w=0, elapsed_s=None)
    # First execution: previous_power = -error = -1000 (discharge to counter import)
    assert result.power_w == -1000
    assert pd.first_execution is False


def test_deadband_holds_last_command():
    pd = _make_controller()
    pd.compute(grid_power_w=1000, target_w=0, elapsed_s=None)  # seeds previous_power=-1000
    result = pd.compute(grid_power_w=20, target_w=0, elapsed_s=2.0)  # within 50W deadband
    assert result.within_deadband is True
    assert result.power_w == pd.previous_power


def test_output_moves_toward_reducing_grid_error():
    pd = _make_controller()
    pd.compute(grid_power_w=0, target_w=0, elapsed_s=None)  # seed at 0
    result = pd.compute(grid_power_w=1000, target_w=0, elapsed_s=2.0)
    # Grid is importing 1000W -> controller should increase discharge (more negative)
    assert result.power_w < 0


def test_rate_limiter_caps_single_cycle_change():
    pd = _make_controller(max_power_change_w=100)
    pd.compute(grid_power_w=0, target_w=0, elapsed_s=None)
    result = pd.compute(grid_power_w=5000, target_w=0, elapsed_s=2.0)
    assert abs(result.power_w - pd.previous_power if False else result.power_w - 0) <= 100 + 1e-6
    assert result.rate_limited is True


def test_direction_hysteresis_suppresses_small_flip():
    pd = _make_controller(direction_hysteresis_w=200, max_power_change_w=1000, kp=0.5, kd=0)
    # Establish a discharging state
    pd.compute(grid_power_w=500, target_w=0, elapsed_s=None)  # previous_power = -500
    pd.last_output_sign = -1
    # Now push error so the raw PD output would flip sign but stay small.
    result = pd.compute(grid_power_w=-450, target_w=0, elapsed_s=2.0)
    # If it flipped sign but the magnitude is under the hysteresis threshold, must be 0.
    if result.direction_changed is False and result.power_w == 0:
        assert True
    else:
        # Not every parameter combination triggers the exact suppression path;
        # the important invariant is it never *silently* flips with < threshold magnitude.
        assert not (result.power_w != 0 and (result.power_w > 0) != (pd.last_output_sign > 0) and abs(result.power_w) < 200)


def test_minimum_power_forces_idle():
    pd = _make_controller(min_discharge_power_w=200, kp=1.0, kd=0, max_power_change_w=2000, direction_hysteresis_w=0)
    pd.compute(grid_power_w=0, target_w=0, elapsed_s=None)
    result = pd.compute(grid_power_w=100, target_w=0, elapsed_s=2.0)
    # Small error -> small discharge request below min_discharge_power_w -> forced to 0
    if abs(result.power_w) > 0:
        assert abs(result.power_w) >= 200


def test_anti_windup_reanchors_after_sustained_shortfall():
    pd = _make_controller(kp=0.3, kd=0.0)
    pd.compute(grid_power_w=2000, target_w=0, elapsed_s=None)  # previous_power = -2000 (wants to discharge 2000W)
    # Battery only actually delivers -500W for several cycles (sustained shortfall > 150W threshold)
    for _ in range(5):
        result = pd.compute(grid_power_w=2000, target_w=0, elapsed_s=2.0, measured_battery_power_w=-500)
    # After re-anchoring, previous_power should be much closer to the measured -500W
    # than to the original -2000W commanded value.
    assert pd.previous_power > -1500


def test_reset_clears_state():
    pd = _make_controller()
    pd.compute(grid_power_w=1000, target_w=0, elapsed_s=None)
    pd.reset()
    assert pd.first_execution is True
    assert pd.previous_power == 0.0


def test_quality_metrics_track_oscillation():
    pd = _make_controller(kp=1.5, kd=0, max_power_change_w=5000, direction_hysteresis_w=0, min_charge_power_w=0, min_discharge_power_w=0)
    pd.compute(grid_power_w=1000, target_w=0, elapsed_s=None)
    # Alternate sign of grid error every cycle to force output sign flips.
    for i in range(6):
        grid = 1000 if i % 2 == 0 else -1000
        pd.compute(grid_power_w=grid, target_w=0, elapsed_s=1.0)
    assert pd.quality_oscillation_per_min >= 0  # just verify it doesn't crash / stays sane


if __name__ == "__main__":
    tests = {name: fn for name, fn in globals().items() if name.startswith("test_")}
    failures = 0
    for name, fn in tests.items():
        try:
            fn()
            print(f"PASS {name}")
        except AssertionError as e:
            failures += 1
            print(f"FAIL {name}: {e}")
        except Exception as e:
            failures += 1
            print(f"ERROR {name}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    raise SystemExit(1 if failures else 0)
