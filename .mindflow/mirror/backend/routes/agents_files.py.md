---
code_file: backend/routes/agents_files.py
last_verified: 2026-05-14
stub: false
---

## 2026-05-14 — recursive tree + nested raw / delete (pointer-model rework)

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

The flat "top-level files only" listing became a **recursive directory tree**
so the workspace viewer (Config → Workspace) can browse the structure the
agent actually builds (a typical artifact is a folder of files). Server-side
filtering: any directory or file whose name starts with `.` is hidden — the
UI never has to know about `.cache`, `.git`, hidden tooling state.

New endpoints:
- `GET /{agent_id}/files/raw?path=…` — download / inline-preview a single
  file. The viewer uses this for the "download" and "preview" actions, and
  the manual "register as artifact" action targets `path` directly.
- `DELETE /{agent_id}/files/{path:path}` — accepts nested relative paths and
  works for both files (unlink) and directories (rmtree). The previous
  flat-only signature is replaced.

Schema changes: `FileInfo` gained `name` / `path` / `is_dir` /
`children: Optional[List[FileInfo]]`; `FileListResponse.files` was renamed to
`tree`. Pure shape change (no backward compat — frontend is updated in the
same change).

# agents_files.py — agent workspace file management

## Why it exists

Each `(agent_id, user_id)` has its own workspace under
`{base_working_path}/{agent_id}_{user_id}/`. The agent reads/writes there
via the Claude Agent SDK with `cwd=workspace` and a workspace-containment
guard. This router is the **frontend's window** into that workspace:

- list the tree to render the workspace section of the agent config panel,
- download / preview any single file,
- upload a file from the user's machine into the workspace root,
- delete any file or subdirectory.

Together with the manual `POST /api/agents/{aid}/artifacts/register` endpoint
in `agents_artifacts.py`, this lets a user pick a workspace file and turn it
into an artifact tab without going through the LLM at all.

## Upstream / Downstream

Upstream:
- Frontend `FileUpload.tsx` (workspace section of the config panel).
- Frontend artifact "register from workspace" modal.

Downstream:
- `xyz_agent_context.settings.base_working_path` — workspace root.
- `xyz_agent_context.utils.file_safety` for upload-time filename validation.
- `os.scandir` / `shutil.rmtree` for tree walking and recursive delete.

Mounted under `/api/agents` (see `backend/main.py`).

## Design decisions

**Dotfolders are filtered server-side, not client-side.** Hidden tooling state
(`.cache`, `.git`, `.venv`) is not the user's concern; never sending it to
the client also removes the "what if the UI accidentally shows them" footgun.

**Symlinks are not followed when walking** (`scandir(follow_symlinks=False)`)
to neutralise cycles and out-of-workspace escapes. They're silently skipped
if present (`is_dir` / `is_file` both return False with follow_symlinks=False).

**Tree sort order: directories first, files second, each alphabetical.** Makes
the rendering deterministic and matches what every file browser does.

**Path resolution is one shared helper.** `_resolve_within_workspace` rejects
empty paths, null bytes, `..`, and any dotfolder segment, then `realpath`-
resolves and verifies the result still lives under the workspace. Used by
the raw and delete endpoints — anything that consumes a user-supplied path
goes through it.

**Upload still targets the workspace root.** Users uploading files don't pick
a target directory; they drop into the workspace root and the agent can move
them. Keeping upload simple matches the existing UX; nested upload can be
added later if there's demand.

## Gotchas

- **`{agent_id}_{user_id}` directory naming.** A `user_id` containing `_`
  technically makes the directory ambiguous to a human reader, but the path
  resolver doesn't parse the directory name back out — it just joins the
  two values — so there is no actual conflict.

- **Delete is destructive.** `shutil.rmtree` removes a whole subtree without
  prompting. The confirm popup lives in the frontend; the API trusts the
  caller. Path confinement still applies — you can't delete outside the
  workspace.

- **No ownership check.** The route builds a path from `agent_id + user_id`
  via query params and trusts the JWT middleware to gate that. If the
  middleware is bypassed (e.g. local mode) anyone can delete anything for any
  `(agent_id, user_id)` pair — the same posture as the rest of the
  agent-scoped routes. Cloud-mode middleware enforces ownership for the
  whole `/api/agents/` prefix.

- **Symlinks are skipped on listing but resolved on raw / delete.** If a
  symlink target lives inside the workspace, raw/delete will follow it
  (realpath resolution); if it escapes, the workspace-containment check
  refuses with 400. The tree listing never shows symlinks, so a user
  cannot click-to-delete one from the UI; the API still works.
