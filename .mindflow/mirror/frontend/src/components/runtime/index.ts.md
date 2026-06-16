---
code_file: frontend/src/components/runtime/index.ts
last_verified: 2026-06-10
---

# index.ts — Public re-export barrel for the runtime directory

Re-exports `RuntimePanel`, `NarrativeList`, `EventCard`. Consumers import
from `@/components/runtime`.

## 2026-06-10 — RuntimePanel export removed

RuntimePanel retired (bookmark-strip redesign); barrel now exports
NarrativeList + EventCard only.
