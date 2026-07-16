"""Grid-import safety backstop: never exceed the contracted/breaker limit,
regardless of what the PD loop, predictive charging, or a price/EV block
decided. This is a hard override applied last, every cycle — it must hold
even while normal zero-export control is bypassed (e.g. during grid
charging or a price-based discharge block).

A pure function so it's trivially unit-testable without any HA/entity
plumbing.

Sign convention: battery power +charging / -discharging. Grid power meter
+import / -export.
"""
from __future__ import annotations


def apply_capacity_protection(
    power_w: float,
    *,
    raw_grid_w: float,
    previous_power_w: float,
    max_contracted_power_w: float,
    max_discharge_capacity_w: float,
) -> float:
    """`raw_grid_w` is the live meter reading while `previous_power_w` (the
    command that was active when that reading was taken) was in effect —
    both are required to correctly project what the grid position would be
    under a *new* candidate command `power_w`. Passing raw_grid_w alone and
    assuming it already excludes the battery's contribution would silently
    under- or over-count the projection by the previous command's power.
    """
    if max_contracted_power_w <= 0:
        return power_w

    house_load_w = raw_grid_w - previous_power_w
    projected_import_w = house_load_w + power_w
    if projected_import_w <= max_contracted_power_w:
        return power_w

    overshoot_w = projected_import_w - max_contracted_power_w
    # How far power_w can be pushed down (toward/into discharge) before
    # hitting the discharge capacity limit.
    headroom_w = max(0.0, power_w + max_discharge_capacity_w)
    reduction_w = min(overshoot_w, headroom_w)
    return power_w - reduction_w
