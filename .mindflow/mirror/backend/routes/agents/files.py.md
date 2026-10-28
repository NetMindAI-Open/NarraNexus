---
code_file: backend/routes/agents/files.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28（复审后）— workspace 根字面化、拒绝符号链接、目录树上限

- `_get_workspace_path` 对解析出的 workspace 目录做 `refuse_symlinked_dir`：租户能把自己的
  workspace 目录换成指向别人目录的链接，`resolve()` 之后"在 workspace 内"就成了"在受害者
  目录内"。链接一律 404，三个 handler 的宽 except 都显式放行 `HTTPException`（`delete_file`
  里曾留下第二个不可达的 `except HTTPException`，PR #421 复审后删掉）。
- `_resolve_within_workspace` 不再对 workspace 做 `resolve()`（根由
  `resolve_existing_workspace` 以 realpath(base)+字面尾段给出），只解析候选文件；候选路径上
  任何链接都会落到根外被拒。
- 目录树加 `MAX_TREE_NODES=5000` / `MAX_TREE_DEPTH=16`，超限停止并在响应 `truncated=true`
  里说明（事故里一次请求吐了 124MB，且一个文件很多的正常 workspace 也能拖垮后端）。
  `truncated` 只在**真有可见条目被略掉**时置位：节点预算耗尽时仍有待列条目，或深度触顶的
  目录下至少有一个非点号条目（触顶的空目录 = 完整列表，不置位；PR #421 复审 Minor 7）。
  前端 `FileUpload` 据此在树下渲染"部分列表"提示（`awareness.workspace.partialListing`，
  10 份 locale），让被截掉的文件读作"未显示"而不是"不见了"（铁律 #16）；失败分支
  （`success=False`，`truncated` 默认 False）不渲染该提示。

## 2026-10-28 — agent_id 段先确权，再碰磁盘（跨租户读事件）

事件：外部账号用 `GET /api/agents/../files` 与 `files/raw?path=<user>/<agent>/…` 读走了
246 个用户的 workspace 文件。根因是四个 handler 只校验 `?path=` 相对部分，`{agent_id}`
段原样进入 `resolve_existing_workspace` → nested 布局下 `{base}/{user_id}/..` 就是整个
base，`_resolve_within_workspace` 看到的"workspace"也是 base，于是任何用户的
`<user>/<agent>/x` 都在"范围内"。DELETE 同理可越权。

修法：四个 handler 的第一行从"解析身份"改为 [[_ownership]] 的 `require_owned_workspace_agent`
——先过 `safe_id_segment`（见 [[workspace_paths]]），再查 `agents.created_by == 当前用户`，
不满足一律 404（不区分"不存在"与"不是你的"，避免存在性探测）。只有通过的 agent_id 才
进入 `_get_workspace_path`。`?path=` 的既有校验保持不变，它管的是 workspace 内部。

两道栅栏各司其职：DB 确权把路径钉在本用户真实拥有的目录；`safe_id_segment` 是底层
格式栅栏，保护所有不经过路由的调用方。回归测试在 `tests/backend/test_agents_files_traversal.py`。

## 2026-08-11 — 内部错文案脱敏（安全审计 P2-2）

各 `except` 的 `error=str(e)` 收敛为固定文案；`ValueError`（上传校验）保留原文，其余内部异常只进日志、不回客户端（防泄工作区路径/主机名）。

## 2026-05-14 — recursive tree + nested raw / delete (pointer-model rework)

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

# agents/files.py — agent workspace file management

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
in `agents/artifacts.py`, this lets a user pick a workspace file and turn it
into an artifact tab without going through the LLM at all.

## Upstream / Downstream

Upstream:
- Frontend `FileUpload.tsx` (workspace section of the config panel).
- Frontend artifact "register from workspace" modal.

Downstream:
- `narranexus.platform.settings.base_working_path` — workspace root.
- `narranexus.platform.utils.file_safety` for upload-time filename validation.
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
