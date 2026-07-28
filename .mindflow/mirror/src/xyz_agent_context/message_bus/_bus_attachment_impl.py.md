---
code_file: src/xyz_agent_context/message_bus/_bus_attachment_impl.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — meta sidecar + off-loop disk writes + public facade

- **Meta sidecar (`{file_id}_meta.json`)**: `store_bus_attachment_meta` /
  `load_bus_attachment_meta` persist the server-built attachment dict next to
  the staged file. Written by the team-chat upload endpoint after Whisper
  finishes; at send time the route reloads THIS instead of trusting the
  client's echoed dict (transcript is raw prompt injection otherwise — PR
  #141 review). Underscore (not dot) in the name keeps the sidecar invisible
  to `resolve_shared_file_by_id`'s `{file_id}.*` glob so it can never shadow
  the original.
- **Disk writes moved off the event loop**: `_stage_into`'s link/copy and
  `store_bytes_into_bus`'s `write_bytes` (uploads up to 50 MB) run via
  `asyncio.to_thread`; `store_bytes_into_bus` is now async — callers await it.
- **Cross-package consumers import the [[attachments]] facade**, never this
  module directly (routes/MCP tools were pinned to the private impl).

# _bus_attachment_impl.py — 让 bus 消息携带文件

## 为什么存在

message bus 原本只传纯文本。这个私有实现让 Agent 之间能通过 bus 互发文件/图片
（多模态 A2A）。它不把字节塞进消息，而是**按引用传**：发送时把文件落到"每-user
共享区"，消息里只存一小段元数据 + base 相对路径；投递时由 trigger 渲染成 Read 工具
marker，recipient 用内置多模态 `Read` 直接读。

## 成立的前提（关键洞察）

两条约束咬合让"跨 agent 传文件"变简单，无需 per-recipient 拷贝：
1. bus **只允许同 user**（`local_bus` 跨 user `PermissionError`）；
2. Executor 按 **per-user 挂载** `{base}/{user_id}`（见 [[workspace_paths]]）。

所以同 user 下所有 agent 共享一个文件根，共享区 `{base}/{user_id}/_shared/bus_files/`
对每个 recipient 都物理可见 + 可读（沙箱验证见设计文档 §1.3：codex `"**":"read"` /
claude `bypassPermissions`）。**本地与云端同一条码路**，不分叉——这也是刻意对齐铁律 #7。

## 上下游

- **被谁调用**：`_message_bus_mcp_tools.py`（`bus_send_message` / `bus_send_to_agent`
  的 `attachment_refs` → `resolve_and_stage_refs`；`bus_share_to_team` → `stage_path_into_team`）；
  `message_bus_trigger.py` 两个 prompt builder → `build_bus_markers`。
- **依赖谁**：`attachment_storage`（file_id 解析 + `generate_file_id`）、`workspace_paths`
  （`agent_workspace_path` / `bus_files_dir` / `team_shared_dir`）、`file_safety`
  （`ensure_within_directory` 防逃逸）、`attachment_schema.derive_category_from_mime`。

## 设计决策

- **句柄两类**：`att_` 前缀走 `resolve_attachment_path`（发送方 user_upload_files）；
  否则当作发送方 workspace 相对路径，`is_relative_to(workspace)` 拒绝 `../` 逃逸与绝对路径。
- **落地 hard-link 优先**：`os.link` 零复制，任何 `OSError`（EXDEV 跨设备等）fallback
  `shutil.copy2`。大文件转发不翻倍占盘。
- **存 base 相对路径**（`rel_path`），marker 构建时 join `base_working_path` → 绝对。
  抗 base 漂移，与 `instance_artifacts.file_path` 同思路；**不复用**
  `Attachment.synthesize_marker`（那是 user_upload_files 专用解析），而用 `build_bus_markers`
  产出**同格式** marker（`… — use Read tool to view]`），保持 recipient 行为与 user 上传一致。
- **marker 无需 recipient 的 agent_id/user_id**：路径已是共享区绝对路径，不再二次解析——
  天然绕开 group channel 多 recipient 的 per-recipient staging 难题。

## 2026-07-21 — voice memos: transcript in marker + by-id resolver

`build_bus_markers` now appends `transcript=…` when a bus-attachment dict carries one, so a
recipient agent reads a voice memo's words inline. Added `resolve_shared_file_by_id(user_id,
file_id)` — globs `_shared/bus_files/*/{file_id}.*` (prefers the original over an `.mp3`
transcode sibling), used by [[transcription_public]] as the fallback when the agent-scoped
resolver misses (team voice memos live in the shared area, not user_upload_files).

## 2026-07-21 — store_bytes_into_bus (user uploads)

Added `store_bytes_into_bus(user_id, raw_bytes, original_name, mime_type)` — the upload
counterpart to `resolve_and_stage_refs`. A human attaching a file to a team message has no
source workspace file to reference, so this writes the raw bytes straight into
`{base}/{user_id}/_shared/bus_files/{date}/` and returns the same bus-attachment dict.
Shared dict-building/target helpers (`_bus_att_dict`, `_new_target`) were factored out so
the link-from-file path (`_stage_into`) and the write-bytes path share one shape. Called by
`teams.py`'s team-chat upload endpoint. MIME is passed in (server-sniffed by the route),
not guessed here.

## Gotcha

- `resolve_and_stage_refs` **永不抛**：单个坏引用 log warning 后跳过，不能因一个附件
  失败而丢整条消息（与铁律：附件是增强，不是消息主体）。
- `sender_agent_id`/`owner_user_id` 必须来自运行时认证上下文，**绝不接受 LLM 传参**
  （与 attachment_storage 同规约）。MCP 工具里 owner 由 `agents.created_by` 反查，不信任 LLM。
- 共享区**无 `_index.json`**：DB 里的 attachment dict 就是索引，rel_path 直接可解析。
- team 共享区写入必须走服务端工具：cloud 沙箱下 agent 只能写自己 workspace，`_shared`
  对 agent 是 read-only，所以 `bus_share_to_team` 由（非沙箱的）MCP server 进程代写。
