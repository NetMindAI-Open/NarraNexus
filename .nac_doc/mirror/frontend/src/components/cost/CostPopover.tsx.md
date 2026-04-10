---
code_file: frontend/src/components/cost/CostPopover.tsx
last_verified: 2026-04-10
---

# CostPopover.tsx — Token usage popover in the top navbar

Trigger button shows a live token count badge. Popover shows total in/out
tokens, per-model breakdown (sorted by usage), and a 5-day daily trend.
Supports two views: "Agent" (current agent, 7 days) and "All" (all agents
combined, 7 days).

## Why it exists

Token costs are an operational concern — users need to see consumption trends
without navigating away from the agent chat.

## Upstream / downstream

- **Upstream:** `usePreloadStore` (costSummary for current agent, refreshCost),
  `api.getCosts('_all')` for the all-agents view (loaded on first tab switch)
- **Used by:** top navbar / header bar

## Design decisions

**Lazy load "all agents" data:** The all-agents summary is only fetched when
the user first clicks the "All" tab, not on mount. This avoids an unnecessary
API call that most sessions never need.

**`refreshCost(agentId)` in preloadStore:** The agent-specific data is
already cached in preloadStore and shared with other panels. The popover
doesn't own a separate query — it calls `refreshCost` to invalidate and
re-fetch the shared cache.

## Gotchas

`shortModelName` strips date suffixes (e.g., `claude-3-5-sonnet-20241022` →
`claude-3-5-sonnet`). The regex covers both `YYYY-MM-DD` and `YYYYMMDD`
formats. Anthropic occasionally introduces model IDs with different date
patterns — check if the display breaks when new models are added.
