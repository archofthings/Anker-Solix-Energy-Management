"""Constants for the Anker Solix Energy Manager integration.

Design note: this integration does NOT talk Modbus/cloud to the Anker units
itself. It reads/writes the entities already exposed by the official
`ha-anker-solix-official` integration (state read + `number`/`select`
service calls). That keeps this component decoupled from Anker's transport
layer entirely — it only knows about entity_ids, configured per battery in
the config flow.
"""
from __future__ import annotations

DOMAIN = "anker_solix_energy_manager"
PLATFORMS = ["sensor", "number", "select", "switch"]

# ---------------------------------------------------------------------------
# Config entry keys — global
# ---------------------------------------------------------------------------
CONF_GRID_POWER_SENSOR = "grid_power_sensor"  # signed W, +import / -export
CONF_BATTERIES = "batteries"  # list[dict] of per-battery entity config (see below)
CONF_MIN_CYCLE_INTERVAL = "min_cycle_interval_s"
CONF_MAX_CONTRACTED_POWER = "max_contracted_power_w"  # breaker limit, peak-shaving cap

DEFAULT_MIN_CYCLE_INTERVAL = 2.0
DEFAULT_MAX_CONTRACTED_POWER = 5750  # 3-phase 25A-ish default; user must confirm breaker rating

# ---------------------------------------------------------------------------
# Config entry keys — Phase 2 optional features (all off unless configured)
# ---------------------------------------------------------------------------
CONF_SOLAR_POWER_SENSOR = "solar_power_sensor"  # instantaneous W, feeds consumption_tracker
CONF_SOLAR_FORECAST_REMAINING_SENSOR = "solar_forecast_remaining_sensor"  # kWh remaining today (Solcast/Forecast.Solar)
CONF_EV_CHARGER_POWER_SENSORS = "ev_charger_power_sensors"  # list[str], 0-2 entity_ids
CONF_EV_DISCHARGE_BLOCK_THRESHOLD_W = "ev_discharge_block_threshold_w"

DEFAULT_EV_DISCHARGE_BLOCK_THRESHOLD_W = 1000

CONF_PRICE_SENSOR = "price_sensor"
CONF_PRICE_FORECAST_ATTRIBUTE = "price_forecast_attribute"  # optional dotted attr holding hourly forecast
CONF_PRICE_CHEAP_PERCENTILE = "price_cheap_percentile"

DEFAULT_PRICE_CHEAP_PERCENTILE = 30  # cheapest 30% of the trailing 24h window counts as "cheap"
PRICE_MIN_SAMPLES_BEFORE_ACTIVE = 20  # don't gate charging on a percentile computed from noise

CONF_PREDICTIVE_CHARGING_ENABLED = "predictive_charging_enabled"
CONF_PREDICTIVE_CHARGING_MODE = "predictive_charging_mode"
CONF_PREDICTIVE_TARGET_SOC = "predictive_target_soc"
CONF_PREDICTIVE_COVERAGE_HOURS = "predictive_coverage_hours"
CONF_PREDICTIVE_FIXED_SLOTS = "predictive_fixed_slots"  # list[{"start": "HH:MM", "end": "HH:MM"}]
CONF_PREDICTIVE_CHARGE_POWER_W = "predictive_charge_power_w"

PREDICTIVE_MODE_FIXED_SLOTS = "fixed_time_slots"
PREDICTIVE_MODE_DYNAMIC_PRICING = "dynamic_pricing"
PREDICTIVE_MODE_REALTIME_PRICE = "realtime_price"
PREDICTIVE_MODE_OPTIONS = [PREDICTIVE_MODE_FIXED_SLOTS, PREDICTIVE_MODE_DYNAMIC_PRICING, PREDICTIVE_MODE_REALTIME_PRICE]

DEFAULT_PREDICTIVE_TARGET_SOC = 80
DEFAULT_PREDICTIVE_COVERAGE_HOURS = 8.0
DEFAULT_PREDICTIVE_CHARGE_POWER_W = 1500

# Minimum time an active/inactive predictive-charging decision holds before
# it's allowed to flip again — a flat backstop against chatter regardless of
# which gate is noisy (SOC hovering at target, a reactive price percentile
# hovering at its threshold, a shortfall estimate hovering near zero).
PREDICTIVE_CHARGING_MIN_DWELL_S = 300

# Consumption tracker
CONSUMPTION_HISTORY_DAYS = 7
CONSUMPTION_STORE_VERSION = 1
CONSUMPTION_STORE_KEY = f"{DOMAIN}_consumption_history"
# Sane fallback average (kWh/day) used only until enough real history accumulates.
DEFAULT_FALLBACK_DAILY_CONSUMPTION_KWH = 15.0
# Sanity ceiling for a single instantaneous home-power sample: no residential
# setup plausibly sees this, so a reading above it is a sensor glitch (bad
# Modbus frame, unit mismatch, ...), not real consumption — clamped rather
# than trusted, so it can't corrupt the 7-day rolling average for a week.
MAX_PLAUSIBLE_HOME_POWER_W = 30000

# ---------------------------------------------------------------------------
# Per-battery entity keys (one dict per battery in CONF_BATTERIES)
# ---------------------------------------------------------------------------
BATTERY_NAME = "name"
BATTERY_OPERATING_MODE_ENTITY = "operating_mode_entity"  # select.*_operating_mode
BATTERY_TARGET_GRID_POWER_ENTITY = "target_grid_power_entity"  # number.*_target_grid_power
BATTERY_GRID_FLOW_ENTITY = "grid_flow_entity"  # select.*_grid_flow
BATTERY_SOC_ENTITY = "soc_entity"  # sensor.*_soc (%)
BATTERY_DEVICE_STATUS_ENTITY = "device_status_entity"  # sensor.*_device_status
BATTERY_CHARGING_POWER_ENTITY = "charging_power_entity"  # sensor.*_charging_power (W)
BATTERY_DISCHARGING_POWER_ENTITY = "discharging_power_entity"  # sensor.*_discharging_power (W)
# Optional, user-confirmed semantics: SOC percentage (0-100), the battery's
# own "don't charge above X% / don't discharge below Y%" longevity setting
# (set via the Anker app). Respected as an additional, tighter boundary on
# top of the physical 0/100% bounds — see power_distribution.py's
# available_batteries(). Even if the device's own firmware also enforces
# this independently, third_party_control mode's whole point is that an
# external controller is now responsible, so this isn't assumed to be a
# redundant belt-and-braces check.
BATTERY_CHARGE_LIMIT_ENTITY = "charge_limit_entity"  # number.*_charge_limit, max SOC % (optional)
BATTERY_DISCHARGE_LIMIT_ENTITY = "discharge_limit_entity"  # number.*_discharge_limit, min SOC % (optional)
BATTERY_CAPACITY_WH = "capacity_wh"
BATTERY_MAX_CHARGE_W = "max_charge_w"
BATTERY_MAX_DISCHARGE_W = "max_discharge_w"

# Anker Solix Solarbank Max AC published specs (used only as config-flow defaults —
# always overridable per unit).
DEFAULT_BATTERY_CAPACITY_WH = 7000
DEFAULT_BATTERY_MAX_POWER_W = 3500

# Fallback charge/discharge SOC limits (%) used when charge_limit_entity /
# discharge_limit_entity aren't configured, or are temporarily unreadable —
# i.e. no additional restriction beyond the physical 0/100% bounds.
DEFAULT_CHARGE_LIMIT_SOC = 100.0
DEFAULT_DISCHARGE_LIMIT_SOC = 5.0

# Anker `number.*_target_grid_power` accepts 0, or 100-3500. Values below the
# floor are not meaningful setpoints and are treated as "idle" by the adapter.
ANKER_TARGET_GRID_POWER_FLOOR_W = 100
ANKER_TARGET_GRID_POWER_MAX_W = 3500

# select.*_operating_mode option values (per user-confirmed live entities).
MODE_SMART = "smart_mode"
MODE_THIRD_PARTY_CONTROL = "third_party_control"
MODE_CUSTOM = "custom_mode"
MODE_SELF_CONSUMPTION = "self_consumption"
MODE_TOU = "tou_mode"
MODE_SOCKET_OVERLAY = "socket_overlay_mode"
MODE_DYNAMIC_PRICING = "dynamic_pricing"

# select.*_grid_flow option values.
GRID_FLOW_CHARGE = "charge"
GRID_FLOW_DISCHARGE = "discharge"

# How often (seconds) the adapter re-checks that operating_mode is still
# third_party_control. The units are known to silently revert on a :07/:37
# minute wall-clock cycle; checking every control cycle (well under 30 min)
# reliably catches and corrects this before it can leave a battery idle.
MODE_GUARD_CHECK_INTERVAL_S = 30

# ---------------------------------------------------------------------------
# PD (proportional-derivative) zero-export controller
# ---------------------------------------------------------------------------
CONF_PD_KP = "pd_kp"
CONF_PD_KD = "pd_kd"
CONF_PD_DEADBAND = "pd_deadband_w"
CONF_PD_MAX_POWER_CHANGE = "pd_max_power_change_w"
CONF_PD_DIRECTION_HYSTERESIS = "pd_direction_hysteresis_w"
CONF_PD_MIN_CHARGE_POWER = "pd_min_charge_power_w"
CONF_PD_MIN_DISCHARGE_POWER = "pd_min_discharge_power_w"
CONF_PD_TUNING_PROFILE = "pd_tuning_profile"

DEFAULT_PD_KP = 0.30
DEFAULT_PD_KD = 0.25
DEFAULT_PD_DEADBAND = 50
DEFAULT_PD_MAX_POWER_CHANGE = 500
DEFAULT_PD_DIRECTION_HYSTERESIS = 80
DEFAULT_PD_MIN_CHARGE_POWER = ANKER_TARGET_GRID_POWER_FLOOR_W
DEFAULT_PD_MIN_DISCHARGE_POWER = ANKER_TARGET_GRID_POWER_FLOOR_W

# Nominal control-loop period used to normalize rate/derivative terms when the
# event-driven cadence varies (matches CONF_MIN_CYCLE_INTERVAL by default).
PD_NOMINAL_DT_S = 2.0
# EMA time constants (seconds) for the grid-sample filter and the derivative filter.
PD_GRID_FILTER_TAU_S = 6.0
PD_DERIVATIVE_TAU_S = 8.0

PD_PROFILE_CUSTOM = "custom"
# Deliberately conservative starting point given the prior SoC-instability
# history with a different battery brand — "smooth" is the recommended
# first profile, not "balanced".
PD_TUNING_PROFILES = {
    "very_smooth": {CONF_PD_KP: 0.18, CONF_PD_KD: 0.12, CONF_PD_MAX_POWER_CHANGE: 300},
    "smooth": {CONF_PD_KP: 0.25, CONF_PD_KD: 0.20, CONF_PD_MAX_POWER_CHANGE: 450},
    "balanced": {CONF_PD_KP: DEFAULT_PD_KP, CONF_PD_KD: DEFAULT_PD_KD, CONF_PD_MAX_POWER_CHANGE: DEFAULT_PD_MAX_POWER_CHANGE},
    "aggressive": {CONF_PD_KP: 0.45, CONF_PD_KD: 0.35, CONF_PD_MAX_POWER_CHANGE: 900},
    "very_aggressive": {CONF_PD_KP: 0.65, CONF_PD_KD: 0.40, CONF_PD_MAX_POWER_CHANGE: 1500},
}
PD_TUNING_PROFILE_OPTIONS = list(PD_TUNING_PROFILES.keys()) + [PD_PROFILE_CUSTOM]
DEFAULT_PD_TUNING_PROFILE = "smooth"

# Anti-windup: how much sustained shortfall between commanded and measured
# battery power before the controller re-anchors to reality.
PD_SATURATION_BACKCALC_THRESHOLD_W = 150
PD_SATURATION_BACKCALC_CYCLES = 3

# ---------------------------------------------------------------------------
# Multi-battery (2-unit) load sharing
# ---------------------------------------------------------------------------
MULTI_BATTERY_DISCHARGE_CROSSOVER_W = 1500
MULTI_BATTERY_CHARGE_CROSSOVER_W = 1750
MULTI_BATTERY_HYSTERESIS_GAP = 0.10
MULTI_BATTERY_MIN_ACTIVATION = 0.50
MULTI_BATTERY_MAX_ACTIVATION = 0.95
MULTI_BATTERY_SELECTION_HOLD_SECONDS = 120
MULTI_BATTERY_SOC_HYSTERESIS = 5.0
