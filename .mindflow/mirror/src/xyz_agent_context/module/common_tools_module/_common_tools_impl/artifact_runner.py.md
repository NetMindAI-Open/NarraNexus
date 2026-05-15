---
code_file: src/xyz_agent_context/module/common_tools_module/_common_tools_impl/artifact_runner.py
last_verified: 2026-05-14
stub: false
---

## 2026-05-14-r3 — drop "must be in subdirectory" rule; single-file mode at workspace root

We removed `delete_source` entirely (deletion is now registry-only — see the
agents_artifacts and users_artifacts mirror md). With `rmtree` gone, the
former "entry can't sit at the workspace root" hard rule had only one
remaining reason: serving dirname-tree from workspace root would expose all
other files. That's solved without a constraint by **soft-degrading at the
serving layer**: when `artifact_root == workspace`, the public-raw route
refuses sub-path requests (only serves the entry). Sibling-asset capability
becomes opt-in via "put your files in a subdirectory" — a tool-description
hint, not a hard error. The size calculation matches: entry-file size when
at workspace root, recursive dir size otherwise.

## 2026-05-14 — rewritten for the pointer model

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

The whole file was rewritten. The old copy/version model
(`create_text_artifact` writing inline content to a managed `artifacts/` folder,
`upload_binary_artifact` copying a workspace file in, `repo.iterate()` appending
version rows) is gone. There is now **one** function, `register_artifact`, that
registers a pointer to an entry file the agent already wrote in its workspace.

# artifact_runner.py — pointer registration for artifacts

## Why it exists

The agent produces visual deliverables (ECharts JSON, HTML apps, CSV, Markdown,
images, PDFs) by **writing files into its own workspace** — that is its natural
working mode, and it lets a deliverable be multi-file (an entry `index.html`
plus `style.css`, `app.js`, `data.json`, images).

`register_artifact` is the bridge that makes such a workspace file *visible to
the user*. It does not write, copy, or move anything — it validates the entry
path, sizes the artifact root directory, enforces quota, and writes/updates one
`instance_artifacts` row. Content stays in the workspace; the backend serves it
straight off disk.

## The model

- **artifact = entry file + its directory.** `artifact_root = dirname(entry)`.
  The whole root directory is served, so the entry HTML can reference siblings.
- The entry file must live in a **subdirectory** of the workspace, never
  directly in the workspace root — registering the root would expose every
  file the agent owns. `_resolve_entry` enforces this with a guiding error.
- Single-file kinds (echarts JSON / csv / markdown / standalone HTML) are the
  degenerate case: a root directory with one file. Same code path.
- `target_artifact_id` re-registers onto an existing row (overwrites the
  pointer + title/description in place). Kind must match.

## Upstream / Downstream

- **Called by**: `artifact_tool.py` (the `register_artifact` MCP tool) and
  `backend/routes/agents_artifacts.py` (the manual `POST .../register` endpoint).
- **Depends on**: `ArtifactRepository` (DB I/O), `settings.base_working_path`
  (workspace root), `Artifact` / `ArtifactKind` / `CreateArtifactToolResult`.
- **Deliberately does not depend on**: agent_runtime, NarrativeService, any
  other Module — it is a generic tool, not scenario-bound.

## Exception hierarchy (for the MCP wrapper / route layer)

| Exception | code | Trigger |
|---|---|---|
| `ArtifactTooLarge` | 413 | artifact root directory > MAX_ARTIFACT_BYTES (25 MB) |
| `ArtifactNotFound` | 404 | `target_artifact_id` row does not exist |
| `ArtifactKindMismatch` | 400 | re-register kind ≠ existing kind |
| `ArtifactPathEscape` | 400 | entry path missing / not a file / outside workspace / directly in workspace root |
| `ArtifactQuotaExceeded` | 507 | per-user count or byte quota exceeded |
| `ArtifactError` (base) | 400 | kind not in the 7-kind whitelist |

`.code` lets the wrapper map to an HTTP status with no branching.

## Design decisions

- **`realpath` for the path-escape check.** Resolves symlinks: a
  workspace-interior symlink pointing at `/etc/passwd` is still rejected by the
  `startswith(workspace + os.sep)` test. `abspath` alone is not enough.

- **Quota delta on re-register.** `_enforce_quota` receives the *delta* bytes —
  the full size for a new artifact, `(new − old)` for a re-register — so
  re-registering a shrunk artifact never trips the byte ceiling, and the count
  check is skipped entirely for re-registers (no new row).

- **`size_bytes` is the recursive root directory size**, not the entry file
  alone — a multi-file HTML app's quota cost is the whole folder.

- **`MAX_ARTIFACT_BYTES` (25 MB) caps a single artifact**; the per-user
  aggregate quota (count + 100 MB bytes, deploy-mode aware) is enforced on top.

- **No filesystem writes at all.** The old "quota check before writing the
  file" ordering concern is gone — there is nothing to write. The only side
  effect is the DB row.

## Gotchas

- `entry_path` may be absolute or workspace-relative — `_resolve_entry` joins
  relative paths against the workspace root before `realpath`.

- The artifact content is **live**: it points at the agent's real file. If the
  agent later edits or deletes the file/folder, the artifact changes or 404s.
  This is intentional (the whole point of the pointer model); the serving route
  returns 410 when the file is gone.

- `settings.base_working_path` is read at call time (via `_workspace_root` /
  `_relative_to_base`), not cached at import — so tests can monkeypatch it.

- The DB `file_path` is relative to `base_working_path`, so moving the workspace
  only needs a settings change; stored paths still resolve.
