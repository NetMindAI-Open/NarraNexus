---
code_file: frontend/src/components/bookmarks/tabs.ts
last_verified: 2026-06-20
stub: false
---

## 2026-06-20 — MCP strip caption shortened

The `mcp` tab gained `stripLabel: 'MCP'` so the strip caption reads "MCP"
instead of the truncated "MCP SERVE…". The full `label` ("MCP Servers") is kept
for the tooltip / aria-label.

## 2026-06-11 (PM)

`stripLabel` optional short caption for the 64px strip (Social
Network → "Social").



# tabs.ts — Atomic-tab registry (single source of truth)

Owner-decided IA (2026-06-11): tab ids, labels, icons, category
grouping, bookmarkStore-signal→status mapping (deriveTabStatus) and
the opened-clears-info rule (markTabOpened) all live here. The strip
and the panel host both consume this file; adding a tab = one entry
here + one render branch in [[BookmarkPanelHost]].

Status semantics per tab: jobs (failedJobs badge > running spinner >
info dot), inbox (unread badge), awareness (external-update info dot);
others currently 'none'. Attention/badges clear only when the
underlying condition resolves — markTabOpened clears info tiers only.
