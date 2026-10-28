---
code_file: frontend/src/platform/whenContext.ts
last_verified: 2026-09-07
stub: false
---

# platform/whenContext.ts — WhenContext from the stores

## Intent

`useWhenContext({conversationKind, agentId})` builds the object slot predicates are evaluated against: the agent's module list (when the agents list carries `modules`) for `agentHas:`, a curated whitelist of the config store's keys for `setting:`. The host decides what a setting key means; a plugin only names it. Lives outside `registries/` because registries must not import stores (dependency-cruiser rule).

## 2026-09-07 — settings selected via an explicit `SETTING_KEYS` whitelist + `useShallow` (M-5)

`settings` used to be `useConfigStore((s) => s as unknown as Record<string, unknown>)` — the
WHOLE store object. Zustand creates a new top-level state object on every `set()`, so this
selector returned a new reference on EVERY store update, no matter which field changed — even
ones no `setting:<key>` clause reads (auth tokens, the agent list, ...). That defeated the
`useMemo` below it and re-rendered every `useWhenContext` consumer (ChatHeader, Composer,
MessageBubble, Sidebar, TopBar, every AgentRow) on every unrelated store change.

Fixed with an explicit `SETTING_KEYS` array (the only keys a `setting:` clause may ever read) and
`useShallow` from `zustand/react/shallow`: the selector still builds a fresh object every call,
but `useShallow` shallow-compares it against the previous result and returns the OLD reference
when nothing in the whitelist changed. `SETTING_KEYS` is currently empty — no `ConfigState` field
is a genuine plugin-facing setting yet (the store only holds auth/session/agent-list state); this
keeps `settings` referentially stable across every current store update while leaving the
mechanism (whitelist + shallow-select) ready for the first real setting key.
