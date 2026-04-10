---
code_file: frontend/src/components/system/HealthStatusBar.tsx
last_verified: 2026-04-10
---

# HealthStatusBar.tsx — Banner showing aggregate health of all services

Four possible states: loading (spinner), null/unavailable (red), all healthy
(green), some unhealthy (amber with count). Pure display — no state, no
effects.

Upstream: `OverallHealth | null` from the parent page's health-check poll.
Used by: System page.
