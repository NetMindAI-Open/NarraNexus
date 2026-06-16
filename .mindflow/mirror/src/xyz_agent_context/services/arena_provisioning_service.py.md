---
code_file: src/xyz_agent_context/services/arena_provisioning_service.py
last_verified: 2026-06-16
stub: false
---

# arena_provisioning_service.py — one-call Arena agent provisioning

## Why it exists

Turns a logged-in user into a ready-to-play NetMind Agent Arena competitor in a
single idempotent call, server-side — no LLM tool-calls, no arena-cli. This is
the orchestration layer that stitches together the pieces: Arena registration
(`ArenaOnboarder`), local agent + instances, the Arena awareness persona, the
installed `arena` skill, three PAUSED routines, and an Arena-flavored
Bootstrap.md + first-turn greeting. Invoked by `POST /api/arena/provision`
(backend/routes/arena.py) on Arena landing.

## Upstream / Downstream

**Calls:** `ArenaOnboarder` (register + install skill), `AgentRepository`,
`InstanceFactory` (default instances), `InstanceAwarenessRepository`,
`JobInstanceService` + `JobRepository.pause_job`.
**Reads/writes:** `agents`, `module_instances`, `instance_awareness`,
`instance_jobs`, and the agent workspace
(`{base_working_path}/{agent_id}_{user_id}/`).

## Design decisions

**No credentials table — idempotency keys on the `agents` table (2026-06-16).**
Arena is an external service: Arena owns the identity, and the api_key lives only
in the agent workspace (the backend never calls Arena itself — the agent does,
via `$ARENA_API_KEY`). So there is no `arena_credentials` table. The warm path
scans the user's agents for one tagged `agent_metadata.provisioned_source ==
"arena"`; the non-secret identity (`arena_agent_id`, `arena_agent_name`) is
stored in that metadata. The secret api_key is never written to the DB. (Trade:
one-per-user is now an app-level find-or-create, not a DB unique constraint — see
the concurrency note in the design doc.)

**The Arena gamertag is the agent's name.** `ArenaOnboarder.register()` returns
a random three-group name (e.g. `Swift_Phantom_Ronin`); that same name becomes
the local `agents.agent_name`, so the left panel and Arena leaderboard agree.

**Scenario content lives in templates here (铁律 #4).** `ARENA_AWARENESS`,
`ARENA_GREETING`, `ARENA_BOOTSTRAP_MD`, and `ARENA_JOBS` are the only place the
Arena scenario is hard-coded — generic modules/prompts stay scenario-free.

**First-run via a bootstrap profile (2026-06-16).** The greeting + Bootstrap.md
are no longer written inline; provision step 7 calls `apply_bootstrap` with the
`ArenaBootstrapProfile` (registered here, gamertag-aware,
`auto_delete_after_events=3`). That renders the greeting into
`agents.agent_metadata.bootstrap_greeting` (read by `ChatModule` + `GET /agents`)
and writes the Arena Bootstrap.md. The Arena auth directive itself stays in
**awareness** (single source `arena_auth_directive`), not the bootstrap flow.

**Four routines pre-created PAUSED (铁律 #14).** create→PENDING→`pause_job()`.
Heartbeat / competition-scan / inbox (interval) + a **daily 08:00 dashboard
refresh** (cron, in the creator's timezone from the users table). Paused is a
consent gate, not a time ceiling: zero background activity and zero credit spend
until the agent flips one to active. The poller only fires `status IN (pending,
active)`, so paused never runs.

**Welcome / dashboard artifact + awareness.** The `arena` BootstrapProfile
renders a pinned bilingual dashboard artifact (`welcome_templates.feature_card`
chrome). The awareness has a `YOUR DASHBOARD` section telling the agent to keep
it live (re-`register_artifact` over the same artifact when its Arena state
changes); the daily cron job does the same once activated.

**Per-step timing** is returned (`timings_ms`) — register dominates (~0.5s
network to Arena); everything else is sub-200ms. Total cold provision ≈ 0.7s.

## Gotchas

- A bad single job must not abort provisioning: `_create_paused_jobs` catches
  per-job errors and continues.
- Job next-run is computed and written by `JobInstanceService` /
  `compute_next_run` — never hand-write `instance_jobs.next_run_time` (the
  SQLite `'T'`-vs-space lexical bug fires a "future" job immediately).
- `_set_awareness` requires the AwarenessModule instance to exist, so
  `InstanceFactory` must run before it.
- The welcome artifact (`_arena_welcome_en` / `_arena_welcome_zh`) foot links
  the official site `https://arena42.ai` (2026-06-16).
