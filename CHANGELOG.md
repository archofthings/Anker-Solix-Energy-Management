# Changelog

## 0.1.0 (unreleased)

Initial build. See the README's "Scope: what's built vs. what's still open"
section for the full feature list and known limitations — nothing has been
run against real hardware yet, so treat this as a pre-release.

- Adapter to the official `ha-anker-solix-official` integration's entities,
  with the third-party-control mode-revert quirk guard.
- Incremental PD zero-export controller with EMA filtering, rate limiting,
  direction hysteresis, deadband, and anti-windup.
- Two-battery load sharing (SOC-ordered selection + proportional allocation).
- Capacity protection safety backstop.
- Derived consumption tracking, EV-session load exclusion.
- Predictive/scheduled grid charging (fixed slots, reactive real-time
  pricing, optional day-ahead forecast lookahead).
- Live PD tuning entities, diagnostic sensors, manual-mode and predictive-
  charging kill switches.
- 59-test pytest suite, including end-to-end control-cycle tests against a
  real (test) Home Assistant instance.
