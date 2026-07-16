# Changelog

## 0.1.0 (unreleased)

Initial build. See the README's "Scope: what's built vs. what's still open"
section for the full feature list and known limitations — nothing has been
run against real hardware yet, so treat this as a pre-release.

- Adapter to the official `ha-anker-solix-official` integration's entities,
  with the third-party-control mode-revert quirk guard.
- Incremental PD zero-export controller with EMA filtering, rate limiting,
  direction hysteresis, deadband, and anti-windup.
- Two-battery load sharing (SOC-ordered selection + proportional allocation),
  respecting each battery's own configured charge/discharge SOC limit if set.
- Capacity protection safety backstop.
- Derived consumption tracking, EV-session load exclusion.
- Predictive/scheduled grid charging (fixed slots, reactive real-time
  pricing, optional day-ahead forecast lookahead).
- Live PD tuning entities, diagnostic sensors, manual-mode and predictive-
  charging kill switches.
- Reconfigure flow: fix a mistake or update a sensor/battery entity without
  removing and re-adding the integration.
- Config flow validation: non-empty/unique battery names, no accidental
  duplicate entity selection between the two batteries, fixed-slot mode
  requires an actual slot.
- English and Dutch translations.
- GPL-3.0 licensed, HACS-installable (custom repository), repo-local brand
  icon.
- 102-test pytest suite, including end-to-end control-cycle and config-flow
  tests against a real (test) Home Assistant instance. A dedicated
  safety/correctness review pass fixed several issues found this way before
  any reached real hardware — see the README's Testing section for details.
