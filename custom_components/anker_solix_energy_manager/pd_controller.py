"""Incremental proportional-derivative (PD) zero/target-export grid controller.

A single-purpose, battery-agnostic class: given a grid power reading and a
target, it outputs a signed aggregate battery power command (+charge /
-discharge). It knows nothing about batteries, entities, or HA.

The anti-oscillation mechanisms below are deliberate, not incidental — the
prior hardware (three Marstek Venus units) was returned for SoC/grid-flow
instability, so a naive "output = Kp * error" controller is not an option:

- Incremental control: each cycle adjusts the *previous* commanded power by
  a P+D correction, rather than recomputing an absolute output from scratch.
  This is inherently smoother under a noisy/quantized grid sample.
- Time-constant EMA filtering on both the raw grid sample and the derivative
  term, so the loop reacts to real trends, not single-sample noise — critical
  since the derivative of an unfiltered signal amplifies noise.
- A rate limiter (max W change per cycle) and a direction-change hysteresis,
  so the output can't slew or flip-flop faster than the physical battery (or
  a human) would consider reasonable.
- Anti-windup via saturation back-calculation: if the battery sustainedly
  fails to deliver the commanded power (SoC/voltage taper, ramp lag), the
  controller's internal state is re-anchored to the *measured* power rather
  than assuming the command was obeyed — otherwise the incremental term
  winds up and causes an overshoot once the load changes.
- A deadband, so the controller does not chase sub-threshold noise at all.

Deliberately no integral (Ki) term: the incremental P term already behaves
like integral action (see above), and dropping Ki removes an entire
anti-windup/leaky-integrator subsystem for a two-battery deployment where the
extra term is unlikely to be needed. Revisit only if steady-state error
proves non-zero in practice.
"""
from __future__ import annotations

import math
import time
from dataclasses import dataclass, field

from .const import (
    PD_DERIVATIVE_TAU_S,
    PD_GRID_FILTER_TAU_S,
    PD_NOMINAL_DT_S,
    PD_SATURATION_BACKCALC_CYCLES,
    PD_SATURATION_BACKCALC_THRESHOLD_W,
)


@dataclass
class PDResult:
    power_w: float  # signed: +charge / -discharge
    error_w: float
    within_deadband: bool
    direction_changed: bool
    rate_limited: bool


@dataclass
class PDController:
    kp: float
    kd: float
    deadband_w: float
    max_power_change_w: float
    direction_hysteresis_w: float
    min_charge_power_w: float = 0.0
    min_discharge_power_w: float = 0.0
    nominal_dt_s: float = PD_NOMINAL_DT_S
    grid_filter_tau_s: float = PD_GRID_FILTER_TAU_S
    derivative_tau_s: float = PD_DERIVATIVE_TAU_S

    # -- internal state (persists across calls) --
    _grid_filter_ema: float | None = field(default=None, init=False)
    _derivative_filtered: float = field(default=0.0, init=False)
    previous_power: float = field(default=0.0, init=False)
    previous_error: float = field(default=0.0, init=False)
    last_output_sign: int = field(default=0, init=False)
    first_execution: bool = field(default=True, init=False)
    _saturation_cycles: int = field(default=0, init=False)

    # -- quality metrics (for a diagnostic sensor) --
    _quality_rms_ema: float | None = field(default=None, init=False)
    _quality_osc_ema: float = field(default=0.0, init=False)
    _quality_last_ts: float | None = field(default=None, init=False)
    sign_changes: int = field(default=0, init=False)

    def reset(self) -> None:
        """Drop all controller state (e.g. after manual mode / a long pause)."""
        self._grid_filter_ema = None
        self._derivative_filtered = 0.0
        self.previous_power = 0.0
        self.previous_error = 0.0
        self.last_output_sign = 0
        self.first_execution = True
        self._saturation_cycles = 0

    def filter_grid_sample(self, raw_w: float, elapsed_s: float | None) -> float:
        """Time-constant EMA — smoothing stays constant regardless of the
        actual event-driven cadence between samples."""
        if self._grid_filter_ema is None:
            self._grid_filter_ema = raw_w
        elif elapsed_s is None or elapsed_s > 0:
            dt = elapsed_s if (elapsed_s is not None and elapsed_s > 0) else self.nominal_dt_s
            alpha = dt / (self.grid_filter_tau_s + dt)
            self._grid_filter_ema += alpha * (raw_w - self._grid_filter_ema)
        return self._grid_filter_ema

    def compute(
        self,
        *,
        grid_power_w: float,
        target_w: float,
        elapsed_s: float | None,
        measured_battery_power_w: float | None = None,
    ) -> PDResult:
        """Run one control cycle. `grid_power_w` should already be the
        filtered sample (call `filter_grid_sample` first) so callers can
        inspect the filtered value before deciding whether to run this.
        """
        error = grid_power_w - target_w

        if self.first_execution:
            target_power = -error
            # Ramp in rather than an unbounded first jump: a large grid
            # imbalance already present at startup (e.g. right after a HA
            # restart mid-load) must not produce an instant full-power step
            # just because there's no "previous" command to rate-limit from.
            rate_limited = abs(target_power) > self.max_power_change_w
            if rate_limited:
                target_power = math.copysign(self.max_power_change_w, target_power)
            self.previous_power = target_power
            self.previous_error = -error
            self._derivative_filtered = 0.0
            self.first_execution = False
            self.last_output_sign = 1 if self.previous_power > 0 else (-1 if self.previous_power < 0 else 0)
            return PDResult(self.previous_power, error, False, False, rate_limited)

        if abs(error) < self.deadband_w:
            self.previous_error = error
            self._derivative_filtered = 0.0
            self._saturation_cycles = 0
            self._quality_last_ts = time.monotonic()
            return PDResult(self.previous_power, error, True, False, False)

        # Anti-windup: re-anchor to measured power if the battery has sustainedly
        # under-delivered the commanded setpoint (same sign, meaningful shortfall).
        # "Same sign" must treat measured==0 as consistent with EITHER command
        # direction (a battery that's supposed to be discharging but measures
        # exactly 0W is exactly the under-delivery case this exists to catch) —
        # comparing previous_power>0 against measured>=0 is asymmetric and misses
        # that case when previous_power<0, so both sides use the same >=/<= shape.
        same_direction = (
            (self.previous_power > 0 and measured_battery_power_w is not None and measured_battery_power_w >= 0)
            or (self.previous_power < 0 and measured_battery_power_w is not None and measured_battery_power_w <= 0)
        )
        if (
            measured_battery_power_w is not None
            and self.previous_power != 0
            and same_direction
            and abs(self.previous_power) - abs(measured_battery_power_w) > PD_SATURATION_BACKCALC_THRESHOLD_W
        ):
            self._saturation_cycles += 1
            if self._saturation_cycles >= PD_SATURATION_BACKCALC_CYCLES:
                self.previous_power = measured_battery_power_w
        else:
            self._saturation_cycles = 0

        base_dt = elapsed_s if (elapsed_s and elapsed_s > 0) else self.nominal_dt_s
        real_dt = max(1.0, min(base_dt, 30.0))
        scale_dt = max(0.1, min(base_dt, 30.0))

        error_derivative_raw = (error - self.previous_error) / real_dt
        d_alpha = real_dt / (self.derivative_tau_s + real_dt)
        self._derivative_filtered += d_alpha * (error_derivative_raw - self._derivative_filtered)

        p_scale = scale_dt / self.nominal_dt_s
        if self.kp > 0:
            p_scale = min(p_scale, max(1.0, 1.0 / self.kp))

        p_term = self.kp * error * p_scale
        d_term = self.kd * self._derivative_filtered
        adjustment = p_term + d_term

        new_power_raw = self.previous_power - adjustment

        max_change = self.max_power_change_w * (scale_dt / self.nominal_dt_s)
        power_change = new_power_raw - self.previous_power
        rate_limited = abs(power_change) > max_change
        if rate_limited:
            sign = 1 if power_change > 0 else -1
            new_power = self.previous_power + sign * max_change
        else:
            new_power = new_power_raw

        current_sign = 1 if new_power > 0 else (-1 if new_power < 0 else 0)
        direction_changed = False
        if self.last_output_sign != 0 and current_sign != 0 and self.last_output_sign != current_sign:
            direction_changed = True
            if abs(new_power) < self.direction_hysteresis_w:
                new_power = 0
                current_sign = 0
                direction_changed = False

        if new_power > 0 and self.min_charge_power_w > 0 and new_power < self.min_charge_power_w:
            new_power = 0
        elif new_power < 0 and self.min_discharge_power_w > 0 and abs(new_power) < self.min_discharge_power_w:
            new_power = 0

        sign_changed_for_metrics = current_sign != 0 and self.last_output_sign != 0 and current_sign != self.last_output_sign
        self._update_quality_metrics(error, sign_changed_for_metrics)
        if current_sign != 0:
            self.last_output_sign = current_sign

        self.previous_power = new_power
        self.previous_error = error

        return PDResult(new_power, error, False, direction_changed, rate_limited)

    def freeze(self, held_power_w: float, error: float) -> None:
        """Call instead of `compute()` when an external condition (time slot,
        price block, EV pause, ...) forces a specific power without running
        the PD math, while keeping controller state consistent for next cycle.
        """
        self.previous_power = held_power_w
        self.previous_error = error
        self.last_output_sign = 1 if held_power_w > 0 else (-1 if held_power_w < 0 else 0)
        self._derivative_filtered = 0.0
        self._saturation_cycles = 0

    def _update_quality_metrics(self, error: float, sign_changed: bool) -> None:
        now = time.monotonic()
        if sign_changed:
            self.sign_changes += 1
        if self._quality_last_ts is None:
            self._quality_last_ts = now
            self._quality_rms_ema = error * error
            return
        dt = now - self._quality_last_ts
        self._quality_last_ts = now
        if dt <= 0:
            return
        alpha = dt / (30.0 + dt)
        sq = error * error
        self._quality_rms_ema = sq if self._quality_rms_ema is None else (
            self._quality_rms_ema + alpha * (sq - self._quality_rms_ema)
        )
        inst_per_min = (60.0 / dt) if sign_changed else 0.0
        self._quality_osc_ema += alpha * (inst_per_min - self._quality_osc_ema)

    @property
    def quality_rms_error_w(self) -> float | None:
        if self._quality_rms_ema is None:
            return None
        return math.sqrt(max(0.0, self._quality_rms_ema))

    @property
    def quality_oscillation_per_min(self) -> float:
        return self._quality_osc_ema
