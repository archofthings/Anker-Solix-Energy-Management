"""Derived household consumption tracking.

Simplified from `consumption_tracker.py` in ffunes/Marstek-Venus-Energy-Manager
(1221 lines there — solar-noon/sunrise estimation, per-slot windows, grid-at-
min-SOC history, ...). This version deliberately drops the self-estimated
solar-timing heuristics: we have a real solar *forecast* sensor available
(Solcast / Forecast.Solar via CONF_SOLAR_FORECAST_REMAINING_SENSOR), so
predictive charging's "will solar cover the gap" question is answered from
that forecast directly rather than by modeling sunrise/sunset ourselves.

What this keeps: household consumption is derived (no separate consumption
sensor needed) from the same energy-balance identity —

    home_consumption_w = solar_power_w + grid_power_w - battery_net_charge_w

(grid_power_w is signed: +import / -export; battery_net_charge_w is +charging
/ -discharging) — integrated into a running daily total and averaged over a
trailing window, so predictive charging has a "how much will we use" signal.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import date

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store
from homeassistant.util import dt as dt_util

from .const import (
    CONSUMPTION_HISTORY_DAYS,
    CONSUMPTION_STORE_KEY,
    CONSUMPTION_STORE_VERSION,
    DEFAULT_FALLBACK_DAILY_CONSUMPTION_KWH,
)

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


@dataclass
class ConsumptionTracker:
    hass: HomeAssistant
    entry_id: str
    solar_power_sensor: str | None
    grid_power_sensor: str

    today_wh: float = 0.0
    today_date: date | None = field(default=None)
    history_kwh: deque[float] = field(default_factory=lambda: deque(maxlen=CONSUMPTION_HISTORY_DAYS))
    _store: Store | None = field(default=None, init=False)
    _dirty: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        self._store = Store(self.hass, CONSUMPTION_STORE_VERSION, f"{CONSUMPTION_STORE_KEY}_{self.entry_id}")

    async def async_load(self) -> None:
        data = await self._store.async_load()
        if not data:
            return
        try:
            self.history_kwh = deque(data.get("history_kwh", []), maxlen=CONSUMPTION_HISTORY_DAYS)
            self.today_wh = float(data.get("today_wh", 0.0))
            today_str = data.get("today_date")
            self.today_date = date.fromisoformat(today_str) if today_str else None
        except (TypeError, ValueError) as err:
            _LOGGER.warning("Could not parse stored consumption history, starting fresh: %s", err)

    async def async_save(self) -> None:
        if not self._dirty:
            return
        await self._store.async_save(
            {
                "history_kwh": list(self.history_kwh),
                "today_wh": self.today_wh,
                "today_date": self.today_date.isoformat() if self.today_date else None,
            }
        )
        self._dirty = False

    def _current_home_power_w(self, battery_net_charge_w: float) -> float | None:
        grid_w = _state_float(self.hass, self.grid_power_sensor)
        if grid_w is None:
            return None
        solar_w = _state_float(self.hass, self.solar_power_sensor) if self.solar_power_sensor else 0.0
        solar_w = solar_w if solar_w is not None else 0.0
        home_w = solar_w + grid_w - battery_net_charge_w
        # Household draw can't be meaningfully negative; a brief negative
        # reading here is sensor noise/timing skew across three independent
        # sources, not a real energy-balance violation.
        return max(0.0, home_w)

    def accumulate(self, elapsed_s: float | None, battery_net_charge_w: float) -> None:
        """Integrate the current sample into today's running total.

        Call once per control cycle with the real elapsed time since the
        previous call (None/0 on the very first call — seeds state only).
        """
        today = dt_util.now().date()
        if self.today_date is None:
            self.today_date = today
        elif today != self.today_date:
            self.history_kwh.append(round(self.today_wh / 1000.0, 3))
            self.today_wh = 0.0
            self.today_date = today
            self._dirty = True

        if not elapsed_s or elapsed_s <= 0:
            return
        home_w = self._current_home_power_w(battery_net_charge_w)
        if home_w is None:
            return
        # Clamp a single sample's contribution: a stale/aborted cycle passing
        # a large elapsed_s (e.g. after HA restart) must not inject an
        # implausible energy jump into today's total.
        capped_elapsed_s = min(elapsed_s, 300.0)
        self.today_wh += home_w * capped_elapsed_s / 3600.0
        self._dirty = True

    @property
    def average_daily_kwh(self) -> float:
        if not self.history_kwh:
            return DEFAULT_FALLBACK_DAILY_CONSUMPTION_KWH
        return sum(self.history_kwh) / len(self.history_kwh)

    @property
    def today_kwh(self) -> float:
        return round(self.today_wh / 1000.0, 3)

    @property
    def has_sufficient_history(self) -> bool:
        return len(self.history_kwh) >= 2

    def expected_consumption_over(self, hours: float) -> float:
        """Expected consumption (kWh) over the next `hours`, from the
        trailing-average daily rate. Deliberately time-of-day-agnostic
        (no attempt to model when during the day load happens) — a
        documented simplification vs. the reference project."""
        return max(0.0, self.average_daily_kwh) / 24.0 * max(0.0, hours)
