---
code_file: frontend/src/platform/PluginStatus.tsx
last_verified: 2026-09-04
stub: false
---

# platform/PluginStatus.tsx — pending/failed plugin surface

## Intent

The one loading/failure element page and panel gates show while a plugin is activating or after it failed (`role=alert` with the error). Extracted from gates.tsx so that file exports only gate factories (react-refresh only-export-components).
