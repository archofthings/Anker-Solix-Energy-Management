"""Tests for pd_controller.py's incremental PD control loop."""
from custom_components.anker_solix_energy_manager.pd_controller import PDController


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


def test_first_execution_counters_current_error_within_rate_limit():
    pd = _make_controller(max_power_change_w=5000)
    result = pd.compute(grid_power_w=1000, target_w=0, elapsed_s=None)
    assert result.power_w == -1000
    assert pd.first_execution is False
    assert result.rate_limited is False


def test_first_execution_ramps_in_rather_than_unbounded_jump():
    # A large grid imbalance already present at startup (e.g. right after a
    # HA restart) must not produce an instant full-power step just because
    # there's no "previous" command yet to rate-limit from.
    pd = _make_controller(max_power_change_w=500)
    result = pd.compute(grid_power_w=5000, target_w=0, elapsed_s=None)
    assert result.power_w == -500  # clamped to max_power_change_w, not -5000
    assert result.rate_limited is True
    assert pd.previous_power == -500


def test_deadband_holds_last_command():
    pd = _make_controller()
    pd.compute(grid_power_w=1000, target_w=0, elapsed_s=None)
    result = pd.compute(grid_power_w=20, target_w=0, elapsed_s=2.0)
    assert result.within_deadband is True
    assert result.power_w == pd.previous_power


def test_output_moves_toward_reducing_grid_error():
    pd = _make_controller()
    pd.compute(grid_power_w=0, target_w=0, elapsed_s=None)
    result = pd.compute(grid_power_w=1000, target_w=0, elapsed_s=2.0)
    assert result.power_w < 0


def test_rate_limiter_caps_single_cycle_change():
    pd = _make_controller(max_power_change_w=100)
    pd.compute(grid_power_w=0, target_w=0, elapsed_s=None)
    result = pd.compute(grid_power_w=5000, target_w=0, elapsed_s=2.0)
    assert abs(result.power_w) <= 100 + 1e-6
    assert result.rate_limited is True


def test_direction_hysteresis_never_silently_flips_below_threshold():
    pd = _make_controller(direction_hysteresis_w=200, max_power_change_w=1000, kp=0.5, kd=0)
    pd.compute(grid_power_w=500, target_w=0, elapsed_s=None)
    pd.last_output_sign = -1
    result = pd.compute(grid_power_w=-450, target_w=0, elapsed_s=2.0)
    flipped = result.power_w != 0 and (result.power_w > 0) != (pd.last_output_sign > 0)
    assert not (flipped and abs(result.power_w) < 200)


def test_minimum_power_forces_idle():
    pd = _make_controller(min_discharge_power_w=200, kp=1.0, kd=0, max_power_change_w=2000, direction_hysteresis_w=0)
    pd.compute(grid_power_w=0, target_w=0, elapsed_s=None)
    result = pd.compute(grid_power_w=100, target_w=0, elapsed_s=2.0)
    if result.power_w != 0:
        assert abs(result.power_w) >= 200


def test_anti_windup_reanchors_after_sustained_shortfall():
    # max_power_change_w=5000 so the (now rate-limited) first execution can
    # still establish previous_power=-2000 unclamped, isolating this test to
    # the anti-windup mechanism itself rather than the startup ramp-in.
    pd = _make_controller(kp=0.3, kd=0.0, max_power_change_w=5000)
    pd.compute(grid_power_w=2000, target_w=0, elapsed_s=None)  # previous_power = -2000
    for _ in range(5):
        pd.compute(grid_power_w=2000, target_w=0, elapsed_s=2.0, measured_battery_power_w=-500)
    # Should have re-anchored toward the measured -500W, not stayed near -2000W.
    assert pd.previous_power > -1500


def test_anti_windup_reanchors_symmetrically_when_measured_is_exactly_zero():
    # Regression test: commanding discharge (previous_power<0) while the
    # battery measures exactly 0W (e.g. it hit a protection limit and
    # stopped) must be recognized as "same direction, full shortfall" and
    # trigger re-anchoring — comparing previous_power>0 against
    # measured>=0 on both sides is asymmetric and used to miss this exact
    # case when previous_power<0.
    pd = _make_controller(kp=0.3, kd=0.0, max_power_change_w=5000)
    pd.compute(grid_power_w=2000, target_w=0, elapsed_s=None)  # previous_power = -2000
    for _ in range(5):
        pd.compute(grid_power_w=2000, target_w=0, elapsed_s=2.0, measured_battery_power_w=0.0)
    assert pd.previous_power > -1500


def test_reset_clears_state():
    pd = _make_controller()
    pd.compute(grid_power_w=1000, target_w=0, elapsed_s=None)
    pd.reset()
    assert pd.first_execution is True
    assert pd.previous_power == 0.0


def test_quality_metrics_do_not_crash_on_oscillating_input():
    pd = _make_controller(kp=1.5, kd=0, max_power_change_w=5000, direction_hysteresis_w=0, min_charge_power_w=0, min_discharge_power_w=0)
    pd.compute(grid_power_w=1000, target_w=0, elapsed_s=None)
    for i in range(6):
        grid = 1000 if i % 2 == 0 else -1000
        pd.compute(grid_power_w=grid, target_w=0, elapsed_s=1.0)
    assert pd.quality_oscillation_per_min >= 0
