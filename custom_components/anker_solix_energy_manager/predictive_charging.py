"""Predictive/scheduled grid charging: charge from the grid ahead of an
expected shortfall, gated by whether solar + current battery stock can
plausibly cover expected consumption over a configurable coverage window.

This is the highest-priority decision after manual mode / operation
blockers in the control loop — when active, it bypasses the normal PD
zero-export loop entirely (mirrors the reference project's
`grid_charging_active` early-return). Capacity protection still applies on
top of whatever this decides; it is not a way to bypass the breaker limit.
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, time as dt_time, timedelta

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .adapter import BatteryAdapter
from .const import (
    PREDICTIVE_CHARGING_MIN_DWELL_S,
    PREDICTIVE_MODE_DYNAMIC_PRICING,
    PREDICTIVE_MODE_FIXED_SLOTS,
    PREDICTIVE_MODE_REALTIME_PRICE,
)
from .consumption_tracker import ConsumptionTracker
from .pricing import PriceTracker

_LOGGER = logging.getLogger(__name__)


def _state_float(hass: HomeAssistant, entity_id: str | None) -> float | None:
    if not entity_id:
        return None
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return None
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return None


def _parse_hhmm(value: str) -> dt_time:
    hour, minute = value.split(":")
    return dt_time(int(hour), int(minute))


def _in_slot(now_t: dt_time, start: dt_time, end: dt_time) -> bool:
    if start <= end:
        return start <= now_t < end
    return now_t >= start or now_t < end  # overnight wrap, e.g. 23:00-06:00


@dataclass
class PredictiveChargingManager:
    hass: HomeAssistant
    enabled: bool
    mode: str
    target_soc: float
    coverage_hours: float
    charge_power_w: float
    fixed_slots: list[dict]
    solar_forecast_remaining_sensor: str | None
    consumption_tracker: ConsumptionTracker
    price_tracker: PriceTracker | None
    batteries: list[BatteryAdapter]

    # Anti-chatter dwell-time state (see should_charge's docstring).
    _active: bool = field(default=False, init=False)
    _last_transition_monotonic: float = field(default=float("-inf"), init=False)

    def _solar_forecast_remaining_kwh(self) -> float:
        # No forecast sensor configured -> assume 0 kWh remaining solar.
        # This is the conservative-for-supply direction (more likely to
        # decide charging is needed) at the cost of missing potential
        # savings; configure the forecast sensor to get real solar-aware
        # delay instead of this fallback.
        value = _state_float(self.hass, self.solar_forecast_remaining_sensor)
        return value if value is not None else 0.0

    def _battery_available_kwh(self) -> float:
        total = 0.0
        for b in self.batteries:
            if not b.data.get("available", False):
                continue
            soc = b.data.get("battery_soc", 0.0)
            total += b.capacity_wh / 1000.0 * (soc / 100.0)
        return total

    def coverage_shortfall_kwh(self) -> float:
        expected = self.consumption_tracker.expected_consumption_over(self.coverage_hours)
        solar = self._solar_forecast_remaining_kwh()
        battery = self._battery_available_kwh()
        return max(0.0, expected - solar - battery)

    def _any_battery_below_target(self) -> bool:
        for b in self.batteries:
            if not b.data.get("available", False):
                continue
            if b.data.get("battery_soc", 100.0) < self.target_soc:
                return True
        return False

    def _in_fixed_slot(self, now: datetime) -> bool:
        now_t = now.time()
        for slot in self.fixed_slots:
            try:
                start = _parse_hhmm(slot["start"])
                end = _parse_hhmm(slot["end"])
            except (KeyError, ValueError):
                continue
            if _in_slot(now_t, start, end):
                return True
        return False

    def _price_is_cheap(self, now: datetime) -> tuple[bool, str]:
        if self.price_tracker is None:
            return False, "no price sensor configured"

        if self.mode == PREDICTIVE_MODE_REALTIME_PRICE:
            if not self.price_tracker.has_sufficient_history:
                return False, "building price history"
            return self.price_tracker.is_cheap_now(), "reactive percentile"

        forecast = self.price_tracker.forecast_hours()
        if forecast is None:
            return False, "forecast unavailable"
        threshold_prices = sorted(p for _, p in forecast)
        idx = max(0, min(len(threshold_prices) - 1, round(len(threshold_prices) * self.price_tracker.cheap_percentile / 100.0) - 1))
        threshold = threshold_prices[idx]
        try:
            now_utc = dt_util.as_utc(now)
            current_hour_entries = [
                p for start, p in forecast
                if dt_util.as_utc(start) <= now_utc < dt_util.as_utc(start).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            ]
        except (TypeError, ValueError) as err:
            _LOGGER.warning("Price forecast timestamps not comparable to current time: %s", err)
            return False, "forecast timestamps not comparable"
        if not current_hour_entries:
            return False, "no forecast entry for current hour"
        return current_hour_entries[0] <= threshold, "forecast lookahead"

    def _evaluate(self, now: datetime) -> tuple[bool, str]:
        if not self._any_battery_below_target():
            return False, "batteries at/above target SOC"

        if self.coverage_shortfall_kwh() <= 0:
            return False, "solar + battery stock sufficient for coverage window"

        if self.mode == PREDICTIVE_MODE_FIXED_SLOTS:
            if self._in_fixed_slot(now):
                return True, "fixed slot active + shortfall"
            return False, "outside configured fixed slot"

        if self.mode in (PREDICTIVE_MODE_DYNAMIC_PRICING, PREDICTIVE_MODE_REALTIME_PRICE):
            cheap, reason = self._price_is_cheap(now)
            if cheap:
                return True, f"price cheap ({reason}) + shortfall"
            return False, f"price not cheap ({reason})"

        _LOGGER.warning("Predictive charging: unknown mode %r, treating as disabled", self.mode)
        return False, f"unknown mode {self.mode!r}"

    def should_charge(self, now: datetime | None = None) -> tuple[bool, str]:
        """Gated by a minimum dwell time once active/inactive, so a noisy
        gate (SOC hovering right at target, a reactive price percentile
        flickering, a shortfall estimate near zero) can't rapidly toggle
        grid charging on and off. Disabling the feature takes effect
        immediately — that's an explicit user override, not something to
        smooth over.
        """
        if not self.enabled:
            self._active = False
            return False, "disabled"

        now = now or dt_util.now()
        raw_result, reason = self._evaluate(now)

        if raw_result != self._active:
            elapsed_s = time.monotonic() - self._last_transition_monotonic
            if elapsed_s < PREDICTIVE_CHARGING_MIN_DWELL_S:
                held = "active" if self._active else "inactive"
                return self._active, f"{reason} (holding {held}, dwell time not elapsed)"
            self._active = raw_result
            self._last_transition_monotonic = time.monotonic()

        return self._active, reason
