---
code_file: frontend/src/types/artifact.ts
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — URL-tab types

Added `application/x-url` to `ArtifactKind`; `EmbedMode`, `EmbedVerdict`,
`UrlArtifactDoc`, and `effectiveEmbedMode()` (collapses recommend +
user_override — renderers must use it, not `recommended` directly).
## 2026-05-14 — pointer model (versioning dropped, rawUrl helper removed)

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

`Artifact` mirror of the backend pointer model: dropped `ArtifactVersion`,
`ArtifactWithVersions`, and `latest_version`; added `file_path` (entry file
relative to base_working_path) and `size_bytes` (recursive size of the
artifact root directory).

The sync `rawUrl(agentId, artifactId, version)` helper is gone — raw content
is no longer reachable via a deterministic agent-scoped URL. Callers fetch
the directory-style URL via `artifactsApi.getRawUrl` (which mints a short-TTL
HMAC token), or the `useArtifactRawUrl` hook.

# types/artifact.ts — Artifact domain types

## Why it exists

The artifact system introduces a new first-class resource: agent-generated files (HTML reports, ECharts JSON, CSVs, Markdown, images, PDFs). This file centralizes all TypeScript shapes for that resource so the REST service, Zustand store, and future UI components share a single source of truth without re-declaring the same interfaces.

## Upstream / Downstream

Mirrors the backend's `ArtifactSchema` and `ArtifactVersionSchema` Pydantic models (in `backend/routes/artifacts.py` or `schema/`). Any new field added to the backend response must be reflected here.

Consumed directly by:
- `services/artifactsApi.ts` — return types for all fetch calls
- `stores/artifactStore.ts` — the `artifacts: Artifact[]` slice
- Future UI components (`ArtifactColumn`, `ArtifactViewer`, tab headers)

## Design decisions

**`ArtifactKind` as a string union, not an enum.** Keeps it JSON-serialization-friendly and avoids enum-import boilerplate in every consumer. Seven mime-like values cover the current backend; new kinds are added here and to the backend `ArtifactKind` Python `Literal` together.

**`rawUrl` as a pure helper, not inside `artifactsApi`.** It produces a URL string, not a fetch call. Components (e.g., an `<img src={rawUrl(...)}>` or a PDF `<iframe>`) need the URL directly without going through the API client. Keeping it in the types file avoids a circular dependency (`artifactsApi` → `artifact` types → `artifactsApi`).

**`session_id: string | null`.** An artifact starts life attached to the session that generated it. Pinning it sets `session_id` to `null` on the backend (it becomes "global" to the agent, not tied to any session). The nullable field models this lifecycle explicitly.

## Gotchas

- `latest_version` is a denormalized counter. When `ArtifactWithVersions` is fetched, `versions` is the authoritative list; `latest_version` may lag by one frame if the store receives a WS `artifact.updated` event before re-fetching.
- Do not add free-form `metadata: Record<string, unknown>` here; keep the shape closed to retain exhaustive-check guarantees in switch statements over `kind`.

## 2026-07-13 — office-live kind

`ArtifactKind` 联合新增 `application/vnd.officecli-live`(office 文档实时预览)。与后端 `schema/artifact_schema.py` 的 Literal 保持一致。
