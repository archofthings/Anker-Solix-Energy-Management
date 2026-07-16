"""EV charging session load exclusion.

Two EVs on-site means an EV charging session can draw several kW — without
this, the zero-export PD loop would see that as a grid-import spike and
discharge the batteries to cover it, draining battery capacity to power a
charging session that's usually meant to run from grid or solar directly,
not the home battery.

Two independent behaviours, matching the reference project's `external_loads.py`
`calculate_adjustment()` / `check_ev_charger_state()` split:

- `calculate_adjustment()`: subtract configured EV charger power from the
  signal driving the PD loop, so normal (charge-direction) response ignores
  it entirely.
- `discharge_blocked`: while an EV is drawing above a threshold, block
  battery *discharge* outright (a plain subtraction isn't enough here — the
  adjustment would make the PD loop see a false zero-export success and
  never correct once the EV session ends). Charging is still allowed, so
  solar surplus can still top up the battery during a session.
"""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.core import HomeAssistant


def _state_float(hass: HomeAssistant, entity_id: str) -> float:
    state = hass.states.get(entity_id)
    if state is None or state.state in ("unknown", "unavailable"):
        return 0.0
    try:
        return float(state.state)
    except (TypeError, ValueError):
        return 0.0


@dataclass
class EVLoadExclusion:
    hass: HomeAssistant
    charger_power_sensors: list[str]
    discharge_block_threshold_w: float

    def total_ev_power_w(self) -> float:
        return sum(max(0.0, _state_float(self.hass, s)) for s in self.charger_power_sensors)

    def calculate_adjustment(self) -> float:
        """Positive = reduce the grid signal seen by PD by this much (i.e.
        the battery won't try to cover the EV's draw)."""
        if not self.charger_power_sensors:
            return 0.0
        return self.total_ev_power_w()

    @property
    def discharge_blocked(self) -> bool:
        if not self.charger_power_sensors:
            return False
        return self.total_ev_power_w() > self.discharge_block_threshold_w
