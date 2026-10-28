---
code_file: frontend/src/hooks/useFlashFlag.ts
last_verified: 2026-09-10
stub: false
---

# hooks/useFlashFlag.ts — a boolean that turns itself off

`useFlashFlag(durationMs)` returns `[on, flash]`: `flash()` sets the flag
and schedules it back to `false` after `durationMs`.

Used by the two model editors (`AgentLlmConfigPanel`,
`ModelDefaultsSettings`) for their 2.5s "✓ Saved" confirmation. Both
previously called a bare `setTimeout(() => setSaved(false), 2500)`, which
let the first save's timer clear a second save's confirmation early and
left the timer pending after unmount. One hook keeps the two editors
identical.

- Raising again clears the pending timer first, so the countdown restarts.
- The pending timer is cleared on unmount.

Not exported from `hooks/index.ts`: consumers import it by path, so test
files that mock `@/hooks` are unaffected.
