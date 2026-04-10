---
code_file: frontend/src/components/runtime/NarrativeList.tsx
last_verified: 2026-04-10
---

# NarrativeList.tsx — Three-level expandable tree: Narrative → Instance → Event

Renders the agent's long-term memory as a collapsible accordion. Each
Narrative expands to show its Module Instances, each Instance expands to show
its detail, and ChatModule instances further expand to show a MemoryItem with
Events.

## Why this three-level structure

A Narrative is a conversation context (e.g., "Support chat with Alice").
Inside it live Module Instances — different modules (Chat, Job, Awareness)
each have their own instance with their own state. ChatModule instances
additionally own Events (the actual message-response pairs). This mirrors the
backend's data model exactly.

## Upstream / downstream

- **Upstream:** `usePreloadStore` — `chatHistoryNarratives`, `chatHistoryEvents`
- **Downstream:** `EventCard` (leaf level), `ModuleConfig` in `MODULE_CONFIG`
  maps module class names to icons/colors
- **Used by:** `RuntimePanel` (narrative tab)

## Design decisions

**Cancelled/archived instances are filtered out** at two places: inside
`NarrativeItem` (skips rendering) and inside `StepCard`'s display items
(skips display). This is intentional — users shouldn't see the internal
lifecycle states.

**ChatModule events are filtered by `user_id`** when the instance has a
`user_id` field. Backward compat: if no `user_id`, all narrative events are
shown (older data has no per-instance user association).

**`isInitialized` guard:** The loading skeleton is shown until `lastAgentId`
is non-null, meaning `preloadAll` has run at least once. Without this guard,
the empty state would flash before data arrives.

**`sortedNarratives`:** Sorted by `updated_at` descending so the most recent
conversation is always at the top.

## Gotchas

The `MODULE_CONFIG` map only covers five known module classes. Unknown module
classes fall back to a generic `Box` icon. When adding a new module, add an
entry here or the icon will be generic.
