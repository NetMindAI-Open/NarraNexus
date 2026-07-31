---
code_file: src/xyz_agent_context/migration/applier.py
stub: false
last_verified: 2026-07-30
---

## Why it exists

The **write** half of Agent Migration: `apply_plan(db, user_id, plan, agent_id=None)`
executes a `MigrationPlan` onto a NarraNexus agent — create (or reuse) the agent,
then populate awareness / general memory / skills / per-agent url-MCP / **one
Narrative per imported session**. Returns an `ApplyResult` with per-dimension
counts.

## Design decisions

- **Reuses existing repos/factories** (no HTTP coupling): `AgentRepository.add_agent`
  + `InstanceFactory.create_agent_level_instances` (provisions AwarenessModule et
  al.); awareness via `InstanceAwarenessRepository.upsert`; memory via
  `MemoryEngine.retain` per fact (no batch API exists); url-MCP via
  `MCPRepository.add_mcp`. `user_id` is passed in — the one thing that is
  request-bound (`resolve_current_user_id`) stays in the route.
- **Default NarraNexus skills**: a newly-created agent gets the same `is_default`
  set (netmind-vision, officecli, ...) a normally-created agent gets, via
  `SkillMarketplaceService.install_defaults` — the migration path bypasses the
  `POST /api/auth/agents` route that normally fires this, so it must do it here
  or imported agents lack the built-in skills. Runs BEFORE the imported skills so
  a same-name imported skill still wins (faithful repro overwrites the default
  copy). Only for `created=True`; degrades to a no-op when the registry is
  unreachable (desktop offline). Not fire-and-forget here (unlike the route) —
  apply is awaited and reports counts, so defaults are awaited and surfaced in
  `default_skills_installed`.
- **Faithful-reproduction skills (Owner)**: a skill with a `local_path` is COPIED
  verbatim into `agent_workspace_path(agent_id,user_id)/skills/<name>/` — migration
  reproduces the ORIGINAL agent, not a same-name marketplace skill (which may be a
  different implementation). Marketplace install is only the fallback for a
  name-only skill.
- **Sessions → Narratives, in-process** (`_import_narrative`, 2026-07-30): one
  Narrative per `plan.narratives` entry. `_summarize_session` makes ONE
  `get_helper_sdk().llm_function` call → the AI fields (description /
  current_summary / topic_hint / topic_keywords / dynamic_summary), with a
  deterministic fallback so a failed/absent LLM never breaks import. Then
  `NarrativeService.create_narrative` (sets name/description) → we overwrite the
  fields create doesn't set → `save_narrative_to_db`. NO agent loop, NO embeddings
  (routing is BM25). The session's turns are written TWO ways:
  - **`_seed_chat_history`** → the Narrative's ChatModule instance memory
    (`add_instance_json_format_memory("ChatModule", <chat instance>, ...)`), so
    they load as normal **recent history** when the user opens that narrative.
    ChatModule reads THIS store (`search_instance_json_format_memory`), never the
    event memory below. The message `meta_data.timestamp` is each turn's
    **ORIGINAL** time — a multi-session import writes all narratives at once, and
    the unified timeline sorts by that field, so import-time would collapse the
    cross-narrative ordering. The chat instance id comes from
    `narrative.active_instances` (create_narrative already made it).
    - ⚠️ **Known limitation (multi-session)**: ChatModule's history assembly
      merges the current narrative's long-term with cross-narrative short-term
      recency and caps the unified timeline at `MERGED_HISTORY_MAX` (~30) BY TIME
      (`chat_module.py:546-561`; short-term has no time window since 2026-02-09).
      Imported sessions all have OLD but DIFFERENT timestamps, so opening an
      OLDER session's narrative can see its own seeded turns evicted by a NEWER
      session's more-recent turns. The narrative's `current_summary` /
      `dynamic_summary` still inject, so the agent isn't amnesiac, but "loads as
      recent history" holds cleanly only for single-session / most-recent-session
      imports. A proper fix (floor the current narrative in the cap) is a
      ChatModule-side change on the owner-facing hot path — deferred.
  - **`MemoryEngine.retain(kind="event")`** `scope_type=narrative` — the
    append-only, searchable per-interaction index (surfaced via the `remember`
    tool). **Do not use `observation`**: it consolidates at threshold 4, so the
    background worker would tombstone the imported turns into summaries ~90s after
    import. Distilled facts (the `memory[]` step) correctly stay `observation`/world.
- Every write is best-effort (per-item try/except) so one failure doesn't abort the
  rest; the result records what landed.

## Gotchas

- Local-skill file-copy needs the backend on the SAME machine as the source
  (desktop/local). On cloud it degrades to a marketplace install / unmatched.
- `AgentRepository.id_field == "id"` (autoincrement), so read with `get_agent(agent_id)`,
  NOT `get_by_id`.
- `_summarize_session` calls `get_helper_sdk()` at call time (module-level import,
  invoked inside the fn) so tests can monkeypatch `applier.get_helper_sdk`.
- helper_llm summary uses the user's configured helper slot → its cost is the
  user's (local). Cloud disables the whole import feature, so no cloud summary.
- ⚠️ **Must resolve the owner's provider config first.** Before the narrative
  loop, `apply_plan` calls `resolve_and_set_provider_for_user(user_id, db,
  agent_id=...)` — the migrate route runs OUTSIDE the per-turn context that
  `AgentRuntime.run` sets, so without this `get_helper_sdk()` falls through to the
  platform default OpenAI key (stale → 401) and every summary silently degrades to
  the deterministic fallback. Same defect + fix as every detached background
  helper task (see `providers.resolver.inject_owner_helper_credentials`). Verified
  live: with it, the summary uses the user's anthropic helper (real keywords);
  without it, 401 → fallback.
