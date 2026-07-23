---
code_file: backend/routes/agents_artifacts.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — URL-tab endpoints

The `POST .../artifacts/url` handler computes the browser origin via
`artifacts_public._app_origin(request)` and passes it to
`ArtifactService.open_url` as `app_origin`, so the self-origin guard is robust
even if `public_base_url` is misconfigured (defense in depth for the
allow-same-origin URL iframe).

Added `POST /{aid}/artifacts/url` (open a web page as a URL tab; SSRF-gated,
probes embeddability) and `POST /{aid}/artifacts/{id}/embed-mode` (set/clear
the user's manual embed override). Both are thin shells over
`ArtifactService.open_url` / `.set_embed_mode` — same auth + error-mapping
pattern as the rest of this router.
## 2026-07-21 — thinned to an HTTP shell; heal moved to ArtifactService

Artifact business logic left this file for the new
`xyz_agent_context/artifact/` package ([[artifact_service.py]]):

- The whole heal recovery strategy (`_KIND_EXTENSIONS`, workspace scan,
  three-step sequence) moved to the service ([[heal.py]]); the endpoint now
  just maps auth + `ArtifactError.code` → HTTPException. `HealResponse` /
  `HealCandidate` became the shared schema models `HealResult` /
  `HealCandidate` (same field names — the wire shape is unchanged).
- Register delegates to `ArtifactService.register` (the same single
  implementation the MCP tool and bootstrap use) instead of importing the
  module-private `_common_tools_impl.artifact_runner`.
- PATCH title now goes through `ArtifactRepository.update_title` instead of a
  raw `db.update("instance_artifacts", ...)` inline in the handler.
- The repeated get+ownership-check pattern collapsed into
  `_get_owned_artifact` (still 404 on mismatch — no existence leak).

References to `artifact_runner` in older entries below are historical; the
code lives in `xyz_agent_context/artifact/_artifact_impl/registration.py` now.

## 2026-05-20 — stale "quota" wording removed

The per-user artifact quota was removed in v1.7.0 (see [[artifact_runner.py]]).
Two leftover mentions of a "validation/quota path" / "same quota" in this doc
were factually wrong (no quota is enforced anywhere) and have been corrected to
just "validation" / path-confinement.

## 2026-05-19 — new POST /{aid}/heal endpoint

Self-heal for artifacts whose pointer is broken (file_path NULL or off-disk
— legacy rows, killed-mid-register processes, agent file moves). Front-end
renderers call this on 410:

1. If the existing pointer is fine on disk → return recovered=True (handles
   transient 410 races).
2. If the caller passed `entry_path` → re-register onto that path
   (target_artifact_id = the artifact). This is the "user picked from the
   modal" path.
3. Otherwise scan the agent workspace for files whose extension matches the
   artifact's kind (`_KIND_EXTENSIONS` table). Sort by mtime desc, cap at
   `_HEAL_MAX_CANDIDATES`. Single match → auto-register. 0 or >1 →
   return `candidates` so the renderer can render `<ArtifactHealModal>` for
   the user to pick.

All registrations route through `artifact_runner.register_artifact` with
`target_artifact_id` set, so path validation / kind whitelist /
MAX_ARTIFACT_BYTES still apply uniformly.

## 2026-05-14-r3 — `delete_source` removed; deletion is registry-only

DELETE no longer accepts `?delete_source=`. The agent's workspace files are
NEVER touched by this endpoint. Reason: the rmtree branch forced a brittle
"entry can't sit at workspace root" register-time rule (else delete-source
wipes the whole workspace) AND introduced a shared-directory footgun (two
artifacts in one dir → one delete wipes both). Both classes of bug
disappear when deletion is registry-only. The user cleans workspace files
via the workspace section in the config panel — same place they already go
to inspect / download / preview files.

Response shape: `{deleted: artifact_id}` (no more `source_deleted`).

## 2026-05-14 — pointer model: raw serving moved to public router

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

This router shed its `/raw` endpoint. Raw content serving now lives in
`artifacts_public.py` under `/api/public/artifacts/raw/{token}/{path}`
(JWT-bypassed; HMAC token in the path is the auth). The split is forced by
the multi-file HTML use case: an iframe `src=` must point at a real URL (a
`blob:` URL breaks relative sub-resource resolution) but native iframe loads
cannot attach Authorization headers — so the raw content must be on a
JWT-exempt path. The token mechanism is in `_artifact_token.py`.

New / changed endpoints in this file:
- POST `/{agent_id}/artifacts/register` — manual register (the workspace tree
  viewer "register as artifact" action). Delegates to
  `artifact_runner.register_artifact` so MCP and UI go through the same
  validation path.
- GET `/{agent_id}/artifacts/{aid}/view-token` — mints a short-TTL HMAC token
  and returns `{token, raw_url, expires_at}`. Frontend calls this once per
  artifact view; `raw_url` is the directory-style URL the iframe `src=` (or
  blob-fetcher) should hit.
- DELETE `/{agent_id}/artifacts/{aid}?delete_source=bool` — `false` (default)
  removes only the DB row; `true` also `rmtree`s the artifact root folder
  (defence-in-depth: confined to the agent workspace AND not equal to it).
- GET detail no longer returns `versions` — under the pointer model there
  is no version list.

`_verify_agent_ownership` is unchanged. The `_resolve_agent_user_id` helper
was added so the manual-register endpoint can find the workspace owner
(matches the `agent_runtime` rule that overrides `ctx.user_id` with
`agent.created_by`).

# agents_artifacts.py

## Why it exists

HTTP boundary for the artifact lifecycle: list, manually register a workspace
file, fetch metadata, mint a view token for raw access, edit pin/title,
delete (optionally with source files). Artifacts are pointers to entry files
the agent wrote in its own workspace; this router is the JWT-authed
control plane on top of them.

Kept separate from `artifacts_public.py` (raw content, token-authed,
JWT-bypassed) — the auth model differs, so the split is structural.

## Upstream / Downstream

Upstream:
- Frontend `artifactStore` / `ArtifactColumn` calls `GET .../artifacts` to
  populate the tab list per session / pinned set.
- Frontend renderers call `GET .../view-token` once per artifact, then load
  raw bytes from the returned `raw_url` (under `/api/public/artifacts/...`).
- Frontend `ArtifactsSection` / `FileUpload` workspace UI calls `PATCH` /
  `DELETE` / `POST .../register`.

Downstream:
- `ArtifactService` (xyz_agent_context/artifact) for register + heal.
- `ArtifactRepository` for plain CRUD (list / get / pin / title / delete).
- `_artifact_token.mint` for view-token minting.

Mounted under `/api/agents` (see `backend/main.py`).

## Design decisions

**Raw content lives on a separate, JWT-exempt router** (`artifacts_public.py`).
Reasoning above — iframe `src=` for multi-file HTML cannot carry an
Authorization header. The HMAC token in the URL path is the auth.

**Manual register and the MCP tool share `ArtifactService.register`.**
Same path-confinement, same kind whitelist, same size cap. (The old "must be
in a subdirectory" hard rule is gone — workspace-root entries are legal
single-file artifacts since 2026-05-14-r3.)

**Deletion is registry-only** (2026-05-14-r3): no `delete_source` parameter
exists anymore; workspace files are never touched by DELETE.

**`agent_id` ownership check on every endpoint**, via `_get_owned_artifact`.
Returns 404 (not 403) for mismatches to avoid leaking existence information.

## Gotchas

- The `view-token` payload decoding (to surface `expires_at`) inlines base64
  url-decode rather than going through `_artifact_token.verify` — `verify`
  would treat its own freshly-minted token as valid, but we want to return
  the `exp` without paying for a second HMAC pass.
- `register_artifact` (manual) is always agent-scoped (`session_id=None`).
  There is no UI flow today to attach a manual registration to a chat
  session; if that's wanted later the route can accept `session_id`.
- The router is mounted at `/api/agents`. The detail / view-token /
  patch / delete routes share the `{artifact_id}` segment; FastAPI does
  not match nested literal paths (`view-token`) against `{artifact_id}`
  because the segment count differs, so ordering between them doesn't
  matter — but `POST .../register` is declared before the bare detail
  GET for clarity.
- Authentication is handled by FastAPI middleware (`backend/auth.py`), not
  here. The handlers only consume `request.state.user_id` for ownership.

## 2026-07-13 — office-live kind 的扩展名映射

`_KIND_EXTENSIONS` 新增 `application/vnd.officecli-live` → (.pptx,.docx,.xlsx),让 heal(按扩展名找回断掉的指针)对 office artifact 也生效。
