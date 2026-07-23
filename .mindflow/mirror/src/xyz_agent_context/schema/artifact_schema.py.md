---
code_file: src/xyz_agent_context/schema/artifact_schema.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — URL_TAB_CONTENT_FILENAME added

Added `URL_TAB_CONTENT_FILENAME = "content.md"` next to `URL_ARTIFACT_KIND`.
It is the naming contract for a URL tab's agent-readable text snapshot, shared
by the writer (url_artifact) and the reader (the common_tools state block).
It lives in schema so the state block imports it from here instead of reaching
across the artifact package's private `_artifact_impl` seam (PR #137 review).

## 2026-07-22 — URL-tab models

Added `URL_ARTIFACT_KIND` ("application/x-url") to `ArtifactKind`, plus
`EmbedMode`, `EmbedVerdict` (with `effective_mode` collapsing recommend +
user_override), and `UrlArtifactDoc` (the on-disk `page.url.json` entry file).
The URL lives in the doc, not a DB column — pointer model preserved.
## 2026-07-21 — HealCandidate / HealResult moved into the central schema

The heal endpoint's response models (`HealResponse` / `HealCandidate`) used to
be route-local pydantic classes in `agents_artifacts.py`. With the heal
strategy promoted into `ArtifactService.heal`, its result type belongs to the
central schema layer: `HealResult` (same fields as the old `HealResponse` —
recovered / artifact / candidates / message — so the wire shape is unchanged)
and `HealCandidate`. Exported via `schema/__init__.py` like the other artifact
models.

## 2026-05-14 — pointer model: versioning dropped

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

`Artifact` switched from a copy/version model to a **pointer model**:
- added `file_path` (entry file relative to `base_working_path`) and
  `size_bytes` (recursive size of the artifact root directory);
- dropped `latest_version`;
- removed `ArtifactVersion` and `ArtifactWithVersions` entirely — there is no
  version table in active use anymore. The DB table `instance_artifact_versions`
  is kept (DEPRECATED) for hand-migration only; see the cleanup TODO.

`CreateArtifactToolResult` dropped its `version` field.

# artifact_schema.py

## Why it exists

Defines the Pydantic shapes for agent-emitted Artifacts — structured outputs
(charts, HTML apps, CSV tables, markdown reports, images, PDFs) that an agent
produces and that the frontend surfaces in a dedicated "Artifacts" tab next to
the chat.

Under the pointer model an Artifact is a **pointer to an entry file the agent
wrote inside its own workspace**. Content is never copied into a managed store.
`file_path` points at the live workspace file; that file's directory is the
artifact root and is served wholesale, so a multi-file HTML app can reference
sibling assets (css/js/json/images).

It ties together:
1. **Persistence**: `Artifact` is the metadata row in `instance_artifacts`.
2. **Tool contract**: `CreateArtifactToolResult` is what the MCP
   `register_artifact` tool returns to the LLM (artifact_id + url).
3. **API transport**: `Artifact` is the read-side shape the REST endpoints serve.

## Upstream / Downstream

**Producers:**
- `ArtifactRepository` creates/updates `Artifact` rows.
- `artifact_runner.register_artifact` validates the entry path and returns a
  `CreateArtifactToolResult`.

**Consumers:**
- `backend/routes/agents_artifacts.py` + `users_artifacts.py` — REST endpoints.
- `frontend/src/types/artifact.ts` — mirrored TypeScript interface.
- `frontend/src/stores/artifactStore.ts` — Zustand store.

## Design decisions

**`ArtifactKind` as a `Literal` string, not an Enum.** Serializes directly to
its string value, readable in DB rows, composes with `Annotated` validators.
Extending the set means adding one string — no migration.

**7 predefined kinds, no "other".** The whitelist enforces that every artifact
is renderable by the frontend. New kinds require a matching frontend renderer.

**`session_id` nullable ⇔ pinned scope.** `session_id is None` ⇔ agent-scoped
("pinned"), survives session cleanup. Set ⇔ session-scoped. The `pinned` boolean
is the user-facing flag; `set_pinned` keeps the two in sync.

**`file_path` is relative to `settings.base_working_path`.** Absolute paths would
break across container restarts / environment migration. It points at the entry
file; `dirname(file_path)` is the artifact root directory.

**`size_bytes` is the artifact root directory size**, not just the entry file —
a multi-file HTML app's quota cost is the whole directory.

**`artifact_id` uses the `art_` prefix + 8 random chars** (铁律 #naming).

## Gotchas

- Do not confuse `Artifact` (this file) with `A2AArtifact` from `a2a_schema` —
  the A2A one is a task-output chunk in the Google A2A protocol, different shape.
- `ArtifactKind` is a `Literal` type alias, not a class. Use
  `x in get_args(ArtifactKind)` for runtime membership checks.
- `pinned` is user intent; `session_id is None` is the DB representation. Keep
  them in sync — always write via the repository.
- `original_session_id` stores the `session_id` captured at pin time so
  `set_pinned(False)` can restore it. `None` on never-pinned / agent-created rows.
- `size_bytes` is populated by the runner at register time (it stats the
  directory). The repository trusts the caller.

## 2026-07-13 — office-live kind

`ArtifactKind` 新增 `application/vnd.officecli-live`:office 文档(pptx/docx/xlsx)渲染成实时 officecli-watch 预览(随 agent 编辑自刷新),非静态文件。
