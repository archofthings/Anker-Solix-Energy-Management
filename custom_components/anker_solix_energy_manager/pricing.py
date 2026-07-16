"""Dynamic/real-time electricity price tracking.

Deliberately does NOT hardcode a Frank Energie (or any other supplier's)
entity/attribute schema — I don't have a verified, current schema for it,
and fabricating one would be worse than not supporting it. Instead:

- `realtime_price` mode is fully self-contained: it samples a single
  "current price" sensor (which effectively every dynamic-pricing HA
  integration exposes, Frank Energie included) into a rolling 24h window and
  computes a percentile threshold from *observed* prices. No assumption
  about attribute names or forecast shape.
- `dynamic_pricing` mode additionally wants true day-ahead lookahead (charge
  at the actual cheapest upcoming hours, not just react once a cheap hour is
  already underway). If `price_forecast_attribute` is configured, it's read
  defensively — accepts a list of dicts each containing a price under any of
  a few common key names and a start time under any of a few common key
  names. If parsing fails or the attribute is absent, this mode logs a
  warning once and reports "forecast unavailable" rather than silently
  falling back to reactive behaviour, since that would quietly change what
  the user configured.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime

from homeassistant.core import HomeAssistant
from homeassistant.util import dt as dt_util

from .const import PRICE_MIN_SAMPLES_BEFORE_ACTIVE

_LOGGER = logging.getLogger(__name__)

_PRICE_KEYS = ("price", "total", "value", "eur_per_kwh")
_START_KEYS = ("start", "from", "datetime", "time")


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
class PriceTracker:
    hass: HomeAssistant
    price_sensor: str
    forecast_attribute: str | None = None
    cheap_percentile: float = 30.0

    _samples: list[tuple[float, float]] = field(default_factory=list)  # (epoch_s, price)
    _forecast_warned: bool = field(default=False, init=False)

    @property
    def current_price(self) -> float | None:
        return _state_float(self.hass, self.price_sensor)

    def record_sample(self) -> None:
        price = self.current_price
        if price is None:
            return
        now = dt_util.utcnow().timestamp()
        self._samples.append((now, price))
        cutoff = now - 24 * 3600
        self._samples = [(t, p) for t, p in self._samples if t >= cutoff]

    @property
    def has_sufficient_history(self) -> bool:
        return len(self._samples) >= PRICE_MIN_SAMPLES_BEFORE_ACTIVE

    def cheap_threshold(self) -> float | None:
        if not self._samples:
            return None
        prices = sorted(p for _, p in self._samples)
        idx = max(0, min(len(prices) - 1, round(len(prices) * self.cheap_percentile / 100.0) - 1))
        return prices[idx]

    def is_cheap_now(self) -> bool:
        """Reactive gate for `realtime_price` mode: current price is at or
        below the trailing-24h cheap-percentile threshold. Conservative
        (False) until enough samples exist to make that threshold meaningful."""
        if not self.has_sufficient_history:
            return False
        threshold = self.cheap_threshold()
        price = self.current_price
        if threshold is None or price is None:
            return False
        return price <= threshold

    def forecast_hours(self) -> list[tuple[datetime, float]] | None:
        """Parsed (start_time, price) pairs from the configured forecast
        attribute, or None if unavailable/unparseable. Used only by
        `dynamic_pricing` mode for genuine lookahead scheduling."""
        if not self.forecast_attribute:
            return None
        state = self.hass.states.get(self.price_sensor)
        if state is None:
            return None
        raw = state.attributes.get(self.forecast_attribute)
        if not isinstance(raw, list):
            self._warn_forecast_once("attribute %r is not a list", self.forecast_attribute)
            return None

        parsed: list[tuple[datetime, float]] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            price = next((entry[k] for k in _PRICE_KEYS if k in entry), None)
            start_raw = next((entry[k] for k in _START_KEYS if k in entry), None)
            if price is None or start_raw is None:
                continue
            start = start_raw if isinstance(start_raw, datetime) else dt_util.parse_datetime(str(start_raw))
            if start is None:
                continue
            try:
                parsed.append((start, float(price)))
            except (TypeError, ValueError):
                continue

        if not parsed:
            self._warn_forecast_once("attribute %r had no parseable entries", self.forecast_attribute)
            return None
        return parsed

    def _warn_forecast_once(self, msg: str, *args) -> None:
        if not self._forecast_warned:
            _LOGGER.warning("Price forecast unavailable: " + msg, *args)
            self._forecast_warned = True
