# Anker Solix Energy Manager

A Home Assistant custom integration that coordinates zero/near-zero grid
export across a pair of **Anker Solix Solarbank Max AC** units: a PD
(proportional-derivative) grid controller plus two-battery load sharing,
built as a thin decision layer on top of the official
[`ha-anker-solix-official`](https://github.com) integration.

It exists as a fallback/enhancement in case Anker's own app-level
coordination between two units proves inadequate — particularly around
avoiding the SoC/grid-flow oscillation that led to returning a prior
three-battery setup from a different vendor.

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
  adapter.py             # entity read/write seam + Custom-Mode-first quirk guard
  pd_controller.py        # battery-agnostic incremental PD zero-export loop
  power_distribution.py   # 2-battery selection + proportional power split
  __init__.py              # wires the three together, config entry setup/timer
  config_flow.py           # setup UI: grid sensor + per-battery entity IDs
  sensor.py / number.py / select.py / switch.py   # diagnostics + live tuning
```

### The Anker mode-revert quirk

The units have been observed silently reverting from `third_party_control`
back to their native operating mode on a `:07`/`:37` minute wall-clock cycle.
Setting `third_party_control` directly does not reliably stick — the fix
(baked into `adapter.py`'s `async_ensure_third_party_control`) is to route
the mode switch through `custom_mode` first, then into `third_party_control`.
This is checked every control cycle, not just once at startup.

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
   power sensor + breaker limit, then each battery's entities and its
   capacity/max power.
5. Start with the **`smooth`** PD tuning profile (`select.pd_tuning_profile`),
   not `balanced` — given the prior instability history, prove out smooth
   control before tightening it. Watch `sensor.pd_control_quality_rms_error`
   and `sensor.pd_oscillation_rate` while tuning.
6. `switch.manual_mode` immediately hands control back to whatever mode/power
   you've set directly (e.g. via the Anker app) without unloading the
   integration — use it if anything looks wrong.

## Scope: what's built vs. what's planned

**Built (this pass):**
- Adapter to the official Anker integration's entities, with the mode-revert
  quirk guard.
- Incremental PD zero-export controller: EMA-filtered grid sample, filtered
  derivative, rate limiting, direction hysteresis, deadband, and anti-windup
  via saturation back-calculation against measured battery power.
- Two-battery load sharing: SOC-ordered selection with hysteresis (prefer one
  active unit below a crossover wattage, split above it) and proportional
  power allocation capped at each unit's limit.
- Live PD tuning (`number.*`/`select.pd_tuning_profile`) and diagnostic
  sensors (control quality RMS error, oscillation rate, active batteries,
  per-battery allocation).

**Not yet built** (deferred — each depends on the above being stable first):
- Consumption tracking (derived rolling-average household demand).
- Predictive/scheduled grid charging (fixed slots, dynamic pricing, real-time
  price) — including a Frank Energy dynamic-pricing integration.
- Hourly net-balance targeting (a specific non-zero grid target per hour,
  rather than pure zero-export) and capacity/peak-shaving protection.
- EV-charging-session load exclusion (so an EV session doesn't distort the
  zero-export target).
- Weekly full-charge / cell-balance scheduling — this may not even be
  meaningful on Solix hardware; needs checking against what the official
  integration already exposes before building anything here.

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
