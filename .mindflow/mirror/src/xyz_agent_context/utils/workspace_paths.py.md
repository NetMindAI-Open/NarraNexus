---
code_file: src/xyz_agent_context/utils/workspace_paths.py
stub: false
last_verified: 2026-06-17
---

## Why it exists

Single source of truth for an agent's on-disk workspace layout. The
layout `{base}/{agent_id}_{user_id}` used to be hardcoded as
`f"{agent_id}_{user_id}"` in ~11 places (step_3, bundle builder/importer/
skill_backup, bootstrap, skill_module, attachment_storage, artifact_runner,
arena_provisioning, identity_migration). This module centralizes it so the
layout can change in ONE place.

## Layout switch

`_LAYOUT` selects the on-disk shape:
- `"flat"` — legacy `{agent_id}_{user_id}`.
- `"nested"` — `{user_id}/{agent_id}` (**current**). This is what lets a
  per-user Executor container bind-mount only `{base}/{user_id}` and thus
  see ONLY that user's agents — cross-user file isolation by mount, no uid
  tricks (the P2 plan, binding rule #20 data-plane).

Flipped flat→nested on 2026-06-17 together with the migration below. All
call sites route through `agent_workspace_relpath` / `agent_workspace_path`,
so the flip was a one-line change here.

## Migration (`migrate_flat_to_nested`)

One-off, idempotent, non-destructive (rename only; never overwrite/delete).
CLI: `scripts/migrate_workspace_layout.py` (dry-run default, `--apply`).

**Disambiguation gotcha (why it takes `known_user_ids`):** a flat dir
`agent_<hex>_<rest>` is ambiguous — `<rest>` could be the user_id directly,
OR the legacy `_user_` infix form (`agent_x_user_binliang` = user
`binliang`, not `user_binliang`). Dir names alone can't tell them apart, so
the migration resolves `<rest>` against the authoritative set of real user
ids from the DB `users` table. Dirs whose owner doesn't resolve are
reported as `unknown` and **left in place — never guessed** (avoids
creating bogus user dirs). Verified on real data 2026-06-17: 284 moved,
0 conflicts, 53 unknown orphans safely left.

## Reader fallback resolvers (avoid a DB rewrite)

The dir migration moves files, but DB columns that store a workspace path
WITH the prefix (notably `instance_artifacts.file_path`, base-relative like
`agent_x_user_y/work/o.html`) are NOT rewritten. Rather than a risky DB
migration (binding rule #6), READERS of existing data use:
- `resolve_existing_workspace(agent_id, user_id, base)` — the workspace dir
  that EXISTS, current layout first then legacy flat / `_user_` fallback.
- `resolve_workspace_relative_file(file_path, agent_id, user_id, base)` —
  resolves a stored base-relative-with-prefix path to a file that exists,
  swapping the prefix flat↔nested if needed.

Wired into every hardcoded flat site the nested flip would otherwise break
(binding rule #8 sweep): `artifacts_public.py`, `agents_artifacts.py`,
`agents_files.py`, `manyfold_files.py`, `auth.py` (workspace delete + the
THREE `bootstrap_active` checks: GET agents, update agent, create agent),
`common_tools_module.py` (artifact list display), `context_runtime.py`
(Bootstrap.md path → bootstrap_active gate), and `_social_mcp_tools.py`
(sub-agent workspace create). So both old (flat) and new (nested) rows
resolve — no DB rewrite, works through the transition forever.

**Why the bootstrap ones mattered:** `apply_bootstrap` writes `Bootstrap.md`
to the nested path, but `bootstrap_active` was checked at the flat path in
3 places → the gate read False → the new-agent greeting + "read Bootstrap.md
and introduce yourself" prompt silently vanished. The audit first missed
these (they key off `created_by`/`owner_user_id` + multiline joins).

## Gotchas

- `_LAYOUT` must be flipped to `"nested"` ONLY after the migration has run
  on a base, or running agents lose their workspace.
- Agent ids are `agent_<hex>` (single token, no internal `_`) — the parse
  relies on this.
- run.sh uses `BASE_WORKING_PATH=/data/workspaces`; the settings default is
  `~/.nexusagent/workspaces` — migrate whichever base a given deploy uses.
- deploy: to bind-mount per `{user_id}`, `workspaces` must be a host
  dir / volume-subpath, not an opaque named volume (deploy-repo change).
