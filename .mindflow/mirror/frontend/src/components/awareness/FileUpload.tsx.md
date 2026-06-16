---
code_file: frontend/src/components/awareness/FileUpload.tsx
last_verified: 2026-06-16
stub: false
---

## 2026-06-16 — workspace Download button now calls downloadFile()

The per-file Download control in `TreeNode` was previously an
`<a href download>` against `api.workspaceFileRawUrl()`. This silently
failed on both the DMG (WKWebView mixed-content block) and `bash run.sh`
(cross-origin, `download` attribute ignored; workspace endpoints also
require `X-User-Id` / `Authorization` headers an `<a>` cannot carry).

The control is now a `<button>` that calls
`downloadFile({ url: downloadUrl, filename: node.name, authHeaders: api.getAuthHeaders() })`
from `lib/download.ts`. Auth headers are required here (unlike artifact
downloads) because workspace file raw endpoints are auth-gated.

## 2026-05-27 — sub-folders default to expanded (P0 fix)

`TreeNode`'s default-expand was `depth < 1`, so only top-level folders
opened on render; sub-folders showed their name but no contents,
easily misread as "sub-folder is ignored". P0 bug 2026-05-18 (Xinyao
Hu). Backend returns the full recursive tree, so showing it all at
once matches the user mental model — they can still collapse with the
chevron. Default is now `true` regardless of depth.

`TreeNode` is now a named export (in addition to the default
`FileUpload`) so tests can render it directly without spinning up the
zustand stores and api wrapper. Test pin in
`__tests__/FileUpload.test.tsx`.

## 2026-05-15 — fix inner-scroll discoverability

Tree's inner `<ScrollArea>` now uses `type="auto"` (always-visible scrollbar when overflow exists) and `max-h-[55vh]` instead of the original `max-h-[260px]` hover-only setup. The previous combination — hidden scrollbar + small cap + outer AwarenessPanel ScrollArea swallowing chained wheel events — made users think the tree couldn't scroll. Paired with `overscroll-contain` becoming a default in `ui/scroll-area.tsx` the wheel now stays inside the tree viewport until its boundary.

## 2026-05-14 — workspace tree viewer + manual register

Spec: `reference/self_notebook/specs/2026-05-14-artifact-pointer-model-design.md`

The flat file list became a **collapsible directory tree** (the backend
already filters dotfolders, so the UI doesn't need to). Per-file actions:

- **Download** — `<a href download>` against `api.workspaceFileRawUrl`.
- **Preview** — opens a `Dialog` and fetches the file via
  `api.fetchWorkspaceFileBlob`. Text files (md/csv/json/txt/html/code) are
  shown in a `<pre>`; images in `<img>`; everything else surfaces a
  "preview unavailable, download instead" message. Text preview is capped
  at 200 KB to keep huge files from freezing the modal.
- **Register as artifact** — opens a `Dialog` with `kind` (auto-detected
  from extension, editable) + `title` (default = filename without ext),
  then calls `artifactsApi.registerFromWorkspace`. Same runner the MCP
  tool uses, so validation is identical (path must live in a workspace
  subdirectory, kind whitelist, quota).
- **Delete** — works on both files and directories (recursive); confirms
  via `useConfirm` before issuing the DELETE.

Top-level drag-and-drop / file-picker upload is unchanged.

# FileUpload.tsx — Workspace tree viewer (config panel)

Hosts the workspace section of the agent config drawer. Lets the user
browse, preview, download, register-as-artifact, delete files and folders
in the agent's workspace, and drag-drop new files into the root.

Used inside `AwarenessPanel`. Owns its own local tree state (no
`usePreloadStore`).
