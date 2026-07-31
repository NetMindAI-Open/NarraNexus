---
code_file: frontend/src/components/bookmarks/index.ts
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — also re-exports `STRIP_WIDTH_PX`

[[MainLayout]] needs the strip width to size the drawer's `edgeReservePx`,
and imports the whole bookmark family through this barrel.

# bookmarks/index.ts — Barrel for the bookmark family

Re-exports [[BookmarkStrip]], [[BookmarkDrawer]], [[BookmarkPanelHost]]
and the [[tabs]] registry. ActivityPanel/AgentProfilePanel removed in
the 2026-06-11 atomic-IA revision.
