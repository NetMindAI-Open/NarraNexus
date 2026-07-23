---
code_file: frontend/src/components/skills/marketplace/SkillDetailSheet.tsx
last_verified: 2026-07-21
stub: false
---

# SkillDetailSheet.tsx

Detail overlay (z-index above the browser): description, scan verdict with
low-issue count, capabilities chips, config_schema key chips (preview of
what the user will need to fill), version history, Install button that
reuses the browser's single install mutation (one pending state, one error
surface). Deliberately read-only about config — actual credential entry
stays in the existing EnvConfigDialog after install.
