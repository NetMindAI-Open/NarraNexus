---
code_file: backend/routes/_dashboard_helpers.py
last_verified: 2026-07-07
stub: false
---

# _dashboard_helpers.py — pure helpers behind GET /api/dashboard/agents-status

## Why it exists

The agents-status endpoint has to fan a single dashboard poll out into many
per-agent aggregations (last activity, live jobs, recent events, today's
metrics, in-progress module instances, enhanced signals) and then collapse
them into permission-correct response models. Keeping that logic out of the
route handler stops every endpoint from re-deriving "what is this agent doing
right now" and gives the privacy/visibility rules one enforcement point. It is
deliberately pure-ish: small sync transformers plus async DB aggregators, no
route/FastAPI coupling.

## How it works / design

- `to_response` is the privacy gate: owner sees [[_dashboard_schema]]'s
  `OwnedAgentStatus` (full detail); a non-owner of a public agent gets the
  stripped `PublicAgentStatus` with `bucket_count` fuzzing the running count;
  private-non-owned returns `None` and the route drops it. `extra='forbid'` on
  the public model is the second line of defense if a field leaks here.
- `build_action_line` / `build_run_state_for_agent` deliberately avoid stale
  `final_output` for running agents (it is null mid-run, Step-4 persistence);
  they read `instance_jobs.description` / `bus_messages.content` instead. All
  text is control-char stripped + UTF-8-codepoint truncated to 80 — XSS and
  layout-break defense even though React escapes at render.
- `classify_kind` maps `working_source` → the `AgentKind` enum that the
  frontend rail/verb logic keys off (see [[api.ts]], [[healthColors]],
  [[DashboardSummary.tsx]]); `derive_health` and `derive_attention_banners`
  pre-compute the server-driven health bucket and banners so the UI does not
  reinvent severity ordering.
- G3 stale detection: `fetch_instances` buckets in_progress module_instances
  into `active` vs `stale` (older than `STALE_THRESHOLD_SECONDS`, env-tunable),
  whitelisting genuinely long-running modules (`SkillModule`) per binding-rule
  14. Stale instances do NOT count toward running_count/kind — they only feed
  the zombie badge.
- Gotchas: every DB helper is SQLite/MySQL dual-dialect (datetime objects vs
  ISO strings normalized via `isoformat()` / dateutil), so don't assume a
  string. **`trigger` is a MySQL reserved word** — `fetch_recent_events`
  SELECTs it, so it must be backticked (`` `trigger` ``); bare, it raises
  `(1064 ...)` on MySQL and the function's `except` silently returns an empty
  feed. SQLite tolerates it bare, hiding the bug locally (fixed 2026-07-07,
  same class as [[auth]]; regression in
  `tests/backend/test_trigger_reserved_word_sql.py`). Metrics columns that
  don't exist yet (duration, token cost) emit
  `None` on purpose (frontend renders "N/A"). `fetch_jobs` partitions each live
  state independently — never re-union `pending` with active/blocked/paused or
  the route double-counts (the v2.1.1 regression). Recent-events maps
  `trigger == MESSAGE_BUS` to kind `chat` / "Group chat reply" so team group
  events don't fall through to a raw "Message_Bus" label.
