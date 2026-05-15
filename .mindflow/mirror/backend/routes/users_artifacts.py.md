---
code_file: backend/routes/users_artifacts.py
last_verified: 2026-05-14
stub: false
---

## 2026-05-14-r3 — `delete_source` removed from bulk delete

`BulkDeleteRequest` no longer takes `delete_source`; `BulkDeleteResponse`
no longer reports `source_deleted`. Bulk delete is registry-only, in lockstep
with the agent-scoped delete (see [[agents_artifacts.py]] mirror md for the
rationale).

# users_artifacts.py

## Why it exists

User-scoped (cross-agent) artifact endpoints powering the Settings →
Artifacts management UI. Distinct from `agents_artifacts.py` (agent-scoped)
because the management UI needs the full set across every agent the user
owns and the ability to free quota with one bulk delete.

## Endpoints

- `GET    /{user_id}/artifacts`       — list every artifact for `user_id`,
  newest first.
- `GET    /{user_id}/artifacts/quota` — current usage vs. configured limits;
  drives the "8 / 10" headline and the progress bar in the management panel.
- `DELETE /{user_id}/artifacts`       — bulk delete; body
  `{ artifact_ids: [...], delete_source: bool }`. With `delete_source=true`,
  each artifact's root directory (the folder containing its entry file) is
  removed too, path-confined to that agent's workspace.

## Upstream / Downstream

Upstream:
- Frontend `Settings → Artifacts` panel (`ArtifactsSection.tsx`).

Downstream:
- `ArtifactRepository.list_by_user` / `count_for_user` /
  `total_bytes_for_user` / `bulk_delete`.
- `settings.base_working_path` for workspace folder resolution when
  `delete_source=true`.

Mounted under `/api/users` (see `backend/main.py`).

## Design decisions

**Tenant isolation.** Each bulk-delete ID is verified to belong to
`user_id` via `repo.get_by_id(aid)` → `art.user_id == user_id`. Unowned IDs
are dropped into `skipped_not_owned` (never silently deleted) so a tenant
cannot mass-delete another's artifacts by guessing IDs.

**`delete_source` is best-effort per artifact.** A failed `rmtree` is
logged but the batch continues, and the DB row is still removed — matches
the per-row delete contract in `agents_artifacts.delete_artifact` and
avoids leaving half-deleted state across many rows.

**Path confinement.** The artifact root must start with
`{base}/{agent_id}_{user_id}/` AND not equal the workspace itself, defence
in depth against a bad DB row.

## Gotchas

- `delete_source` only deletes the artifact's own root directory — never
  the agent's workspace itself. A pre-pointer-model legacy row with
  `file_path = ""` is skipped silently.
- `bulk_delete.artifact_ids` is capped at 200 entries by Pydantic to avoid
  a giant request DOS'ing the lookup loop.
- `_verify_user_self` is a no-op in local mode; in cloud mode it requires
  the JWT `request.state.user_id` to equal the path `user_id`.
