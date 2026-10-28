---
code_file: frontend/src/platform/actionGate.ts
last_verified: 2026-09-04
stub: false
---

# platform/actionGate.ts — gate entry for a declared action slot

## Intent

For `frontend.ui.slots` entries at an action point (chat header / message actions) the loader registers this plain object: the manifest's label, `when`, `order`, and a `run` that fires `onSlot:<id>`, then runs the real action the plugin registered under the same id in `activate(host)`. Separate from gates.tsx because it is not a component (react-refresh).
