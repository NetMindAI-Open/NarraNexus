---
code_file: frontend/src/components/cost/CostPopover.tsx
last_verified: 2026-08-03
---

## 2026-08-03 — `formatCost` 低于万分之一美元时显示 `<$0.0001`

07-31 那条定的规则（「$0.00 会被读成免费而不是未知」，所以成本仅在 > 0 时显示）原先在
`toFixed(4)` 处漏了一档：`0.00003` 会渲染成 `$0.0000` —— 正是同一个误读，而 embedding
密集的一天就落在这个区间。

**为什么阈值是 0.0001**：它就是 `toFixed(4)` 能表示的最小非零值。选它不是估的 —— 低于这个
数字，四位小数**必然**印出全零，所以这正是「还能诚实显示」与「必须换一种说法」的分界。
调用方已经用 > 0 守过门，所以能走到这里的都是真实非零，那就得说出来。

## 2026-07-30 — cache buckets counted into every displayed total

Every sum (grand total, in/out line, per-model rows + their sort key,
daily rows) now includes `cache_read_tokens` + `cache_creation_tokens`.
`input_tokens` from the API is only the full-rate bucket; on a cache-warm
agent the cache buckets are >99% of the input side, so the popover showed
"input 213" for a 1.2M-token week and ranked the helper above the main
loop (output dominated the visible sum — live case agent_39b2b72b823b).
Backend counterpart: [[agents/cost.py]].
Every cache field is read with `?? 0`: a response from a backend build
predating the fields has no such keys, and undefined in a sum renders
"NaNM" — hit live the same day (hot-reloaded frontend against a
not-yet-restarted backend).

Subline design (owner-picked, refined 07-31): the raw token total reads
scary once cache is counted (1.2M of which is 0.1x-priced reads), so the
visible subline is the hit rate — rate-free math (read / input side).
**Money never appears on the face of the panel** (owner preference):
cost lives in hover tooltips on the token figures — the grand total
carries `totalCost` ("$2.39 total"), each per-model token figure carries
its own bare cost (`by_model[].cost`). Cost tooltips render ONLY when
> 0 — unpriced models book $0 and "$0.00" would read as "free" rather
than "unknown". Raw cache read/write counts sit in the hit-rate line's
tooltip (`cost.popover.cacheDetail`). Keys `cacheHit` / `totalCost` /
`cacheDetail` filled in all 10 locales.

## 2026-07-28 — main/helper role labels

The backend now returns semantic aggregation keys instead of provider names:
`__main_model__` and `__helper_model__`. The popover maps those keys to the
localized `Model usage` and `Helper Model Usage` labels. This keeps the usage
scope visible even when users replace Claude, Codex, Gemini, or either helper
provider. Concrete model IDs remain supported as a defensive fallback and
still use the date-suffix shortening rule.

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

**Provider-neutral aggregation:** The UI labels usage by runtime role rather
than by provider or product brand. This prevents a configured provider name
from being mistaken for the scope of the displayed usage.

## Gotchas

`shortModelName` strips date suffixes (e.g., `claude-3-5-sonnet-20241022` →
`claude-3-5-sonnet`). The regex covers both `YYYY-MM-DD` and `YYYYMMDD`
formats. Anthropic occasionally introduces model IDs with different date
patterns — check if the display breaks when new models are added.
