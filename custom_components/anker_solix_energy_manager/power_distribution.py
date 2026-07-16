"""Two-battery power distribution: which unit(s) run, and how much each gets.

Simplified from `power_distribution.py` in ffunes/Marstek-Venus-Energy-Manager
for the fixed 2-battery case (the reference handles up to 6 heterogeneous
units; here `batteries` is always the configured pair). Kept from the
original:

- Proportional allocation by each battery's own power limit, with iterative
  redistribution of any excess onto the battery(ies) with headroom.
- Minimum-battery selection: below a crossover wattage, prefer running a
  single unit (splitting small loads across two units is less efficient);
  above it, split. SOC-ordered (charge fullest-last, drain fullest-first)
  with hysteresis so the "1 vs 2 active" decision doesn't chatter at the
  threshold.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .adapter import BatteryAdapter
from .const import (
    MULTI_BATTERY_CHARGE_CROSSOVER_W,
    MULTI_BATTERY_DISCHARGE_CROSSOVER_W,
    MULTI_BATTERY_HYSTERESIS_GAP,
    MULTI_BATTERY_MAX_ACTIVATION,
    MULTI_BATTERY_MIN_ACTIVATION,
    MULTI_BATTERY_SELECTION_HOLD_SECONDS,
    MULTI_BATTERY_SOC_HYSTERESIS,
)


def _round_to_5w(value: float) -> int:
    return round(value / 5) * 5


def _battery_limit(battery: BatteryAdapter, is_charging: bool) -> float:
    return battery.max_charge_w if is_charging else battery.max_discharge_w


@dataclass
class PowerDistribution:
    batteries: list[BatteryAdapter]

    active_charge_batteries: list[BatteryAdapter] = field(default_factory=list)
    active_discharge_batteries: list[BatteryAdapter] = field(default_factory=list)
    _charge_hold_until: dict[str, float] = field(default_factory=dict)
    _discharge_hold_until: dict[str, float] = field(default_factory=dict)

    def available_batteries(self, is_charging: bool) -> list[BatteryAdapter]:
        """Batteries that are online and have headroom in the requested direction."""
        result = []
        for b in self.batteries:
            if not b.data.get("available", False):
                continue
            soc = b.data.get("battery_soc", 50.0)
            if is_charging and soc >= 100:
                continue
            if not is_charging and soc <= 0:
                continue
            if _battery_limit(b, is_charging) <= 0:
                continue
            result.append(b)
        return result

    def select_batteries(self, total_power_w: float, available: list[BatteryAdapter], is_charging: bool) -> list[BatteryAdapter]:
        if total_power_w <= 0 or not available:
            self._clear_active(is_charging)
            return []

        if len(available) == 1:
            self._set_active(is_charging, available)
            return available

        crossover_w = MULTI_BATTERY_CHARGE_CROSSOVER_W if is_charging else MULTI_BATTERY_DISCHARGE_CROSSOVER_W
        previous_active = self.active_charge_batteries if is_charging else self.active_discharge_batteries
        hold_until = self._charge_hold_until if is_charging else self._discharge_hold_until
        now = time.monotonic()

        def sort_key(b: BatteryAdapter):
            soc = b.data.get("battery_soc", 50.0)
            is_active = b in previous_active
            if is_charging:
                # lowest SOC first (fill emptiest); active battery gets hysteresis advantage
                return soc - (MULTI_BATTERY_SOC_HYSTERESIS if is_active else 0)
            # highest SOC first (drain fullest)
            effective = soc + (MULTI_BATTERY_SOC_HYSTERESIS if is_active else 0)
            return -effective

        ordered = sorted(available, key=sort_key)

        selected: list[BatteryAdapter] = []
        combined_capacity = 0.0
        activation_threshold = MULTI_BATTERY_MIN_ACTIVATION
        for battery in ordered:
            selected.append(battery)
            limit = _battery_limit(battery, is_charging)
            combined_capacity += limit
            activation_threshold = max(
                MULTI_BATTERY_MIN_ACTIVATION,
                min(MULTI_BATTERY_MAX_ACTIVATION, crossover_w / limit) if limit > 0 else MULTI_BATTERY_MAX_ACTIVATION,
            )
            if total_power_w <= combined_capacity * activation_threshold:
                break

        # Hysteresis around the 1-vs-2 boundary, mirrors the reference's two cases.
        if previous_active:
            for battery in previous_active:
                if battery not in selected and battery in available:
                    limit = _battery_limit(battery, is_charging)
                    first_limit = _battery_limit(selected[0], is_charging) if selected else limit
                    act_thr = max(MULTI_BATTERY_MIN_ACTIVATION, min(MULTI_BATTERY_MAX_ACTIVATION, crossover_w / first_limit)) if first_limit > 0 else MULTI_BATTERY_MAX_ACTIVATION
                    deact_thr = max(MULTI_BATTERY_MIN_ACTIVATION, act_thr - MULTI_BATTERY_HYSTERESIS_GAP)
                    if total_power_w > combined_capacity * deact_thr:
                        selected.append(battery)
                        combined_capacity += limit

            if len(selected) > 1 and selected[-1] not in previous_active:
                last = selected[-1]
                last_limit = _battery_limit(last, is_charging)
                capacity_without_last = combined_capacity - last_limit
                prev_limit = _battery_limit(selected[-2], is_charging)
                act_thr = max(MULTI_BATTERY_MIN_ACTIVATION, min(MULTI_BATTERY_MAX_ACTIVATION, crossover_w / prev_limit)) if prev_limit > 0 else MULTI_BATTERY_MAX_ACTIVATION
                act_thr_with_hyst = min(act_thr + MULTI_BATTERY_HYSTERESIS_GAP, MULTI_BATTERY_MAX_ACTIVATION)
                if total_power_w <= capacity_without_last * act_thr_with_hyst:
                    selected.pop()
                    combined_capacity -= last_limit

        # Minimum split duration: once >1 battery is selected, hold that
        # selection for a while so a brief dip doesn't immediately drop back
        # to one unit (and the reverse) — pure wall-clock, independent of
        # whether a cycle even calls this method (deadband can skip it).
        if len(selected) > 1:
            for battery in selected:
                hold_until[battery.name] = now + MULTI_BATTERY_SELECTION_HOLD_SECONDS

        for battery in previous_active:
            if battery not in selected and battery in available and hold_until.get(battery.name, 0) > now:
                selected.append(battery)

        for name in list(hold_until):
            if hold_until[name] <= now:
                hold_until.pop(name, None)

        self._set_active(is_charging, selected)
        return selected

    def distribute_power(self, total_power_w: float, selected: list[BatteryAdapter], is_charging: bool) -> dict[BatteryAdapter, float]:
        """Proportional allocation by each battery's limit, capped and
        redistributed iteratively so no battery is asked for more than it
        can take."""
        if not selected:
            return {}

        limits = {b: _battery_limit(b, is_charging) for b in selected}
        total_capacity = sum(limits.values())
        if total_capacity <= 0:
            return {b: 0 for b in selected}

        remaining_power = min(total_power_w, total_capacity)
        allocation: dict[BatteryAdapter, float] = {}
        remaining = list(selected)

        while remaining_power > 0 and remaining:
            current_capacity = sum(limits[b] for b in remaining)
            if current_capacity <= 0:
                break
            all_fit = True
            for b in list(remaining):
                share = remaining_power * (limits[b] / current_capacity)
                if share >= limits[b]:
                    allocation[b] = _round_to_5w(limits[b])
                    remaining_power -= limits[b]
                    remaining.remove(b)
                    all_fit = False
            if all_fit:
                for b in remaining:
                    share = remaining_power * (limits[b] / current_capacity)
                    allocation[b] = _round_to_5w(share)
                break

        for b in selected:
            allocation.setdefault(b, 0)
        return allocation

    def _set_active(self, is_charging: bool, selected: list[BatteryAdapter]) -> None:
        if is_charging:
            self.active_charge_batteries = list(selected)
            self.active_discharge_batteries = []
            self._discharge_hold_until.clear()
        else:
            self.active_discharge_batteries = list(selected)
            self.active_charge_batteries = []
            self._charge_hold_until.clear()

    def _clear_active(self, is_charging: bool) -> None:
        if is_charging:
            self.active_charge_batteries = []
            self._charge_hold_until.clear()
        else:
            self.active_discharge_batteries = []
            self._discharge_hold_until.clear()
