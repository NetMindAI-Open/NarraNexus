---
code_file: backend/routes/me.py
last_verified: 2026-06-23
stub: false
---

# me.py — owner-scoped (`/api/me`) read endpoints

## 为什么存在

The "You" workspace ([[YouWorkspace]]) needs data aggregated ACROSS all of a
user's agents, not per-agent. Existing endpoints live under
`/api/agents/{agent_id}/…` (one agent). This router is the owner-scoped
counterpart: identity comes from `resolve_current_user_id` and the join key is
`agents.created_by = user_id`. Registered in [[main]] with prefix `/api/me`.

## 设计决策

- `GET /narratives` — every narrative for any agent the user owns, flattened
  to one "storyline" per row for the Narra Memory timeline. Single indexed
  JOIN (`agents.created_by` × `narratives.agent_id`); `narrative_info` JSON is
  decoded server-side into `name` / `summary` so the client stays dumb.
- `GET /network` — every social entity the user's agents know, for the Nexus
  Network graph ([[NexusNetworkGraph]]). Live entities now live in
  `memory_entity` (`kind='entity'`, `expired_at IS NULL`); the old
  `instance_social_entities` table is tombstoned. Each row is one agent's view;
  rows are **deduped** across agents (`_entity_key` = type + normalised name)
  into one node with `known_by[]`, summed interactions, best familiarity/
  strength. The owner's own entity is flagged `is_self` (the graph centre).
  **Entity type is reconciled against the real agent roster** (2026-06-24): an
  entity whose name matches one of the user's agents is forced to `type=agent`
  even if a peer recorded it as a `user` (the LLM mis-types teammates, e.g.
  Boss seen as a user → should render silicon, not carbon). Reconciled BEFORE
  keying so the same entity still merges.
- `GET /worldview` — one **lens** per agent for the Worldview tab
  ([[WorldviewLenses]]): `sees_you` = the agent's `persona` for the user (from
  its `memory_entity` record where `entity_id == user_id` — strictly the
  agent's view of YOU, not every `subtype='user'` entity), and `worldview` =
  a few of that agent's `memory_observation subtype='world'` rows (salience
  desc). Only agents that hold a view of the user appear.
- **Seeded scaffold narratives are excluded by default**: agent setup creates
  ~8 `is_special='default'` routing buckets (GreetingAndCourtesy, …) that are
  not lived storylines and would swamp the timeline. `?include_default=true`
  brings them back. (`is_special != 'default'` ⇒ a real, lived narrative.)
- `LIMIT` is inlined as an int (clamped by `Query(ge/le)`) to dodge
  parameterised-LIMIT quirks across the SQLite/MySQL dialects; the user_id is
  still a bound `%s` param.

## 新人易踩的坑

- Read-only + identity strictly from `resolve_current_user_id` — never trust a
  client-supplied user id (same lesson as `/api/auth/agents`).
- `agents` owner column is `created_by`; agent display name is `agent_name`.
