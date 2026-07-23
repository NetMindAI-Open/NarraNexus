---
code_file: backend/routes/users_artifacts.py
last_verified: 2026-07-21
stub: false
---

## 2026-07-21 — stale delete_source design notes removed

Two "Design decisions" paragraphs still described the `delete_source`
best-effort rmtree + path confinement — behavior that was removed in
2026-05-14-r3 (bulk delete has been registry-only ever since; the code never
touches workspace files). Doc-only fix; the source is unchanged.

## 2026-05-19 — `/quota` endpoint removed

Per-user artifact quotas were dropped (see [[artifact_runner.py]] 2026-05-19
note). `GET /{user_id}/artifacts/quota` and `QuotaInfo` are gone with them;
the Settings → Artifacts panel renders count-only without a progress bar.

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
owns and the ability to bulk delete.

## Endpoints

- `GET    /{user_id}/artifacts`       — list every artifact for `user_id`,
  newest first.
- `DELETE /{user_id}/artifacts`       — bulk delete; body
  `{ artifact_ids: [...] }`. Registry-only — workspace files stay.

## Upstream / Downstream

Upstream:
- Frontend `Settings → Artifacts` panel (`ArtifactsSection.tsx`).

Downstream:
- `ArtifactRepository.list_by_user` / `bulk_delete`.

Mounted under `/api/users` (see `backend/main.py`).

## Design decisions

**Tenant isolation.** Each bulk-delete ID is verified to belong to
`user_id` via `repo.get_by_id(aid)` → `art.user_id == user_id`. Unowned IDs
are dropped into `skipped_not_owned` (never silently deleted) so a tenant
cannot mass-delete another's artifacts by guessing IDs.

**Registry-only bulk delete** (2026-05-14-r3): there is no `delete_source`
option; workspace files are never touched by this endpoint.

## Gotchas

- `delete_source` only deletes the artifact's own root directory — never
  the agent's workspace itself. A pre-pointer-model legacy row with
  `file_path = ""` is skipped silently.
- `bulk_delete.artifact_ids` is capped at 200 entries by Pydantic to avoid
  a giant request DOS'ing the lookup loop.
- `_verify_user_self` is a no-op in local mode; in cloud mode it requires
  the JWT `request.state.user_id` to equal the path `user_id`.
