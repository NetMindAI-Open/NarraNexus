---
code_file: frontend/src/components/chat/__tests__/chatHeaderTooltips.test.tsx
last_verified: 2026-09-22
stub: false
---

# Chat header entry regressions

Panel icons need accessible names and visible Radix tooltips. Browser additionally
must request the browser drawer, avoid opening without an active agent, and
disappear when its panel is unregistered. A real registry subscription and tooltip
are exercised; surrounding stores and expensive popovers are stubbed.
