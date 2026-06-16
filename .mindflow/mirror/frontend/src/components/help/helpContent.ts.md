---
code_file: frontend/src/components/help/helpContent.ts
last_verified: 2026-06-11
stub: false
---

## 2026-06-11 (PM) — pages

Manifest restructured to HelpPage[] (3 Owner-specified topics). New
anchors: sidebar.manage-agents, sidebar.team-section, chat.messages,
layout.artifacts. `side` → `rail` (left/right/top note placement).

## 2026-06-11

Strip anchors re-pointed after the atomic-IA revision:
bookmarks.activity/bookmarks.agent → bookmarks.strip + bookmarks.jobs.



# helpContent.ts — Annotation manifests (pure data)

One exported array per view; entries reference `data-help-id` anchors.
Density discipline (spec §12.5): **≤ 8 per view**, enforced by a test —
a view that needs more annotations needs less UI, and this list doubles
as a complexity audit. Settings-page manifest deliberately deferred
until the parallel Settings redesign lands (spec §14.7). Used by
[[HelpButton]] / [[HelpOverlay]].
