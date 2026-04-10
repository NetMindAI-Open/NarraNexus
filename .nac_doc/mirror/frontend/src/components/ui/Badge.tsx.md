---
code_file: frontend/src/components/ui/Badge.tsx
last_verified: 2026-04-10
stub: false
---

# Badge.tsx — Status chip with optional live-dot pulse

Six semantic variants (default, accent, success, warning, error, outline). The `pulse` prop adds an animated dot to the left side — used for unread message counts in `ContextPanelHeader` and `AgentInboxPanel`.

Gotcha: `pulse` adds `ml-3` to the children `span` to make room for the dot. If you set `pulse` but the variant doesn't define a dot color (e.g., `variant="outline"`), the dot falls back to `--text-tertiary` (grey).
