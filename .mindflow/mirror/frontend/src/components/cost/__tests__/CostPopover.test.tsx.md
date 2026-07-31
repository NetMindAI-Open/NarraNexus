---
code_file: frontend/src/components/cost/__tests__/CostPopover.test.tsx
last_verified: 2026-07-28
stub: false
---

# CostPopover.test.tsx — usage-label regression contract

The fixture uses the backend's semantic `__main_model__` and
`__helper_model__` keys. The test opens the real popover and verifies that both
keys become provider-neutral localized labels, never raw aggregation keys or
the historical Claude Code brand.
