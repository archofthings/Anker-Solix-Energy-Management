# Anker Solix Energy Manager

A Home Assistant custom integration that coordinates zero/near-zero grid
export across a pair of **Anker Solix Solarbank Max AC** units: an
anti-oscillation PD (proportional-derivative) grid controller, two-battery
load sharing, derived consumption tracking, EV-session load exclusion, and
optional predictive/priced grid charging — built as a thin decision layer on
top of the official `ha-anker-solix-official` integration.

It exists as a fallback/enhancement in case Anker's own app-level
coordination between two units proves inadequate — particularly around
avoiding the SoC/grid-flow oscillation that led to returning a prior
three-battery setup from a different vendor. **Control-loop stability was
the top design priority throughout**, not feature completeness.

## How it fits together

This integration does **not** talk Modbus, cloud, or any Anker protocol
itself. It only reads/writes the entities the official Anker integration
already exposes (`select.*_operating_mode`, `number.*_target_grid_power`,
`select.*_grid_flow`, SoC/power sensors) via `hass.states` and the generic
`number`/`select` services. That keeps the two integrations from racing each
other over the same Modbus TCP session, and means this component has no
Anker-specific protocol code to maintain.

```
custom_components/anker_solix_energy_manager/
  adapter.py               # entity read/write seam + Custom-Mode-first quirk guard
  pd_controller.py          # battery-agnostic incremental PD zero-export loop
  power_distribution.py     # 2-battery selection + proportional power split
  capacity_protection.py    # hard grid-import safety backstop (pure function)
  consumption_tracker.py    # derived household demand, rolling daily average
  ev_exclusion.py           # EV charging session load exclusion / discharge block
  pricing.py                # reactive price percentile + optional forecast lookahead
  predictive_charging.py    # fixed-slot / dynamic-pricing / real-time-price grid charging
  __init__.py                # wires everything together, config entry setup/timer
  config_flow.py             # setup UI: grid sensor, per-battery entities, optional features
  sensor.py / number.py / select.py / switch.py   # diagnostics + live tuning + kill switches
tests/                       # pytest suite (59 tests) — see Testing below
```

### Control cycle order

Each cycle runs, in order: refresh battery state → accumulate
consumption/price samples (always, even in manual mode) → manual-mode check
(stops here if on) → predictive grid charging (bypasses PD entirely if its
conditions are met) → normal PD zero-export control (with EV load exclusion
applied to its input and EV-triggered discharge blocking applied to its
output) → **capacity protection** (a hard backstop applied to whatever the
previous steps decided, so the contracted power limit is never exceeded
regardless of which path produced the command) → battery
selection/allocation and the actual writes.

### The Anker mode-revert quirk

The units have been observed silently reverting from `third_party_control`
back to their native operating mode on a `:07`/`:37` minute wall-clock cycle.
Setting `third_party_control` directly does not reliably stick — the fix
(baked into `adapter.py`'s `async_ensure_third_party_control`) is to route
the mode switch through `custom_mode` first, then into `third_party_control`.
This is checked every control cycle (even ones that don't otherwise write
power), not just once at startup.

## Setup

1. In the Anker app, for **each** battery: Devices → gear icon → **Three-Party
   Control Settings** → enable Modbus TCP.
2. Install/configure the official Anker Solix integration in Home Assistant
   so both units' entities exist.
3. Note the entity IDs for both units (Developer Tools → States) — operating
   mode, target grid power, grid flow, SoC, device status, charging power,
   discharging power.
4. Add this integration (Settings → Devices & Services → Add Integration →
   "Anker Solix Energy Manager") and step through the config flow: grid
   power sensor + breaker limit, then each battery's entities and capacity/
   max power, then an optional "features" step (solar sensors, EV chargers,
   price sensor, predictive charging) — leave anything there blank to keep
   that feature off.
5. Start with the **`smooth`** PD tuning profile (`select.pd_tuning_profile`),
   not `balanced` — given the prior instability history, prove out smooth
   control before tightening it. Watch `sensor.pd_control_quality_rms_error`
   and `sensor.pd_oscillation_rate` while tuning.
6. `switch.manual_mode` immediately hands control back to whatever mode/power
   you've set directly (e.g. via the Anker app) without unloading the
   integration — use it if anything looks wrong. `switch.predictive_charging_enabled`
   is a separate, narrower kill switch for just the grid-charging feature.

## Testing

A 59-test pytest suite covers the pure control-logic modules directly and
exercises the real wiring (adapter → PD → power distribution → capacity
protection → the actual `number`/`select` service calls) through
`pytest-homeassistant-custom-component`'s `hass` fixture — this is a real,
if minimal, Home Assistant core instance, not a hand-rolled mock of one.

```bash
python3 -m venv .venv-test && source .venv-test/bin/activate
pip install -r requirements-test.txt
python3 -m pytest tests/ -v
```

Use Python 3.13 for the test venv — Python 3.14 currently has fixture
incompatibilities with the pinned `pytest`/`pytest-asyncio` versions
`pytest-homeassistant-custom-component` requires (this doesn't affect Home
Assistant itself, only the test tooling).

**What's covered:** the PD controller's deadband/rate-limiting/hysteresis/
anti-windup behavior in isolation; capacity protection's projection math
(including that it only ever adds discharge, never reduces one); 2-battery
selection and proportional allocation, including the invariant that a
battery's share can never exceed its own limit; consumption tracking's
energy-balance derivation, day rollover, and save/load round-trip; EV
exclusion and price-percentile/forecast-parsing edge cases; and — the most
load-bearing tests — five end-to-end scenarios through the real control loop:
a first-cycle discharge response, deadband suppressing writes, manual mode
suppressing all writes, the mode-revert quirk being corrected in the right
order, and capacity protection forcing discharge under a tight contracted
limit. Building and running this suite caught two real bugs before either
would have reached real hardware: `BatteryAdapter` being unhashable (broke
the moment power distribution tried to use battery objects as dict keys —
fixed with `@dataclass(eq=False)`), and a capacity-protection formula that
didn't account for the previously-commanded power when projecting a new
command's effect on grid import (fixed to take `previous_power_w`
explicitly — see `capacity_protection.py`'s docstring).

**What's not covered:** anything requiring live Anker hardware (the mode-
revert timing, real Modbus TCP write latency/rate limits) or a live Frank
Energie price entity (the forecast-attribute parsing is tested against
synthetic data with a few plausible key-name variants, not their actual
schema — see the pricing note below).

## Scope: what's built vs. what's still open

**Built:**
- Adapter to the official Anker integration's entities, with the mode-revert
  quirk guard.
- Incremental PD zero-export controller: EMA-filtered grid sample, filtered
  derivative, rate limiting, direction hysteresis, deadband, and anti-windup
  via saturation back-calculation against measured battery power.
- Two-battery load sharing: SOC-ordered selection with hysteresis (prefer one
  active unit below a crossover wattage, split above it) and proportional
  power allocation capped at each unit's limit.
- Capacity protection: a hard backstop, applied every cycle regardless of
  which decision path produced the command, so grid import never exceeds
  the configured contracted power limit.
- Consumption tracking: household demand derived from solar + grid + battery
  power (no separate consumption sensor needed), integrated into a daily
  total and averaged over a trailing 7-day window, persisted across restarts.
- EV-session load exclusion: configured EV charger power sensors are
  subtracted from what the PD loop reacts to, and battery discharge is
  blocked outright above a configurable EV draw threshold.
- Predictive/scheduled grid charging: fixed time slots, reactive real-time
  pricing (self-collected rolling percentile, works with any price sensor),
  or day-ahead forecast lookahead if a forecast attribute is configured —
  gated by a solar-forecast-aware coverage check so it only fires when solar
  + current battery stock genuinely won't cover expected demand.
- Live PD tuning (`number.*`/`select.pd_tuning_profile`), a predictive-
  charging kill switch, and diagnostic sensors (control quality RMS error,
  oscillation rate, active batteries, per-battery allocation, consumption,
  EV draw, current price).

**Deliberately simplified vs. the Marstek reference this was designed
against** (see each module's docstring for the specific reasoning):
- No integral (Ki) term in the PD controller — the incremental P term
  already behaves like integral action.
- Consumption tracking doesn't model solar timing (sunrise/sunset/solar
  noon) — it uses a real solar forecast sensor (Solcast/Forecast.Solar)
  instead, which the reference project didn't have available.
- No hourly net-balance targeting (a specific non-zero grid target per hour)
  — the target is always 0.
- No weekly full-charge / cell-balance scheduling — unclear this is even
  meaningful on Solix hardware; would need checking against what the
  official integration exposes before building anything here.
- Pricing has no hardcoded Frank Energie schema — see below.

**Open items to verify before connecting to real hardware** — this hasn't
been run against a live Home Assistant instance or real batteries yet:
1. Exact current entity IDs for both battery units (pull from HA Developer
   Tools → States) to fill into the config flow.
2. Whether `number.*_target_grid_power` truly only accepts magnitude with a
   separate `grid_flow` direction select, as assumed throughout — confirm
   against the live entities.
3. Safe write frequency for the Anker Modbus TCP interface — the default
   2s minimum cycle interval is a starting guess, not a validated value.
4. Per-unit capacity/power specs (rated kWh, max charge/discharge W) — the
   config flow defaults to 8000Wh / 3500W but these should be confirmed
   against the actual Solarbank Max AC spec sheet or nameplate.
5. **Frank Energie's actual price sensor schema.** `pricing.py` deliberately
   avoids hardcoding attribute names it can't verify — `realtime_price` mode
   works with any plain price sensor out of the box (self-collected rolling
   percentile), but `dynamic_pricing` mode's day-ahead lookahead needs the
   correct forecast attribute name pointed at in config, and its parser
   (accepting a few common key-name variants) hasn't been validated against
   Frank Energie's real integration.
6. The mode-revert quirk's exact timing (`:07`/`:37`) is taken from your
   description, not re-derived here — the guard checks every 30s regardless,
   so it should self-correct within that window even if the exact minute
   mark is slightly different than described.

## Provenance and licensing note

The PD control approach and multi-battery load-sharing design here are
adapted from **[ffunes/Marstek-Venus-Energy-Manager](https://github.com/ffunes/Marstek-Venus-Energy-Manager)**
(GPL-3.0), used strictly as an architectural/algorithmic reference — no code
was copied; everything here was written fresh against this project's own
adapter interface. That project is itself discontinued, with development
continuing in a successor called "Omnibattery".

Because the control algorithms are *derived from* GPL-3.0-licensed work even
though no source was copied, you should decide on a license for this repo
with that in mind (GPL-3.0 is the safest choice if in doubt) rather than
defaulting to "all rights reserved" — this hasn't been decided yet.

## Disclaimer

This integration writes setpoints directly to your battery hardware. Use at
your own risk — no liability is claimed or implied for any hardware damage,
data loss, or energy cost resulting from its use. Start with conservative PD
tuning and verify behavior over several days before trusting it unattended,
especially heading into winter when solar coverage of a bad decision is
smallest.
