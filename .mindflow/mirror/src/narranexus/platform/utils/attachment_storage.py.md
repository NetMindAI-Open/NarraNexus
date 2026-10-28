---
code_file: src/narranexus/platform/utils/attachment_storage.py
last_verified: 2026-09-13
stub: false
---

## 2026-09-13（PR#401 review 🟡2 同类）— `on_disk_suffix` 与当前轮附件列表编码

- `on_disk_suffix(filename)`：落盘扩展名的唯一来源（本文件 `store_uploaded_attachment` 与总线
  `_bus_attachment_impl._new_target` 共用）。小写、点后只留 `[a-z0-9_+-]`、上限 16 字符，什么都不剩则无后缀。
  这是纵深防御：marker 与附件列表里的 path 现在是精确 JSON 字面量（`exact_literal`），脏后缀已被引号挡住，
  但磁盘上仍不该留下作者选的空白/分隔符（`sanitize_filename` 只拦 NUL/分隔符/traversal，换行能进后缀）。
  MIME 仍以索引里嗅探的为准，后缀只是提示。
- `format_attachments_for_system_prompt` 的每行 `- name="…", type="…", mime="…", path="…"[, transcript="…"]` 是
  marker 的同类行语法，适用同一不变量：**每个值都编码，`path` 用无损的 `exact_literal`（`json.loads` 逐字节等于
  磁盘路径），其余用 `inline_literal`**，只有键名、`FILE_MARKER_UNAVAILABLE_PATH`（与 marker 共用的常量，路径解析
  失败）与 `transcript=<unavailable: …>`（平台固定提示）裸露。`type=` 经 `attachment_schema.category_value`
  统一把枚举还原成值。`mime_type` 可能回显外部声明的 Content-Type、
  `category` 来自 WS payload 的普通字符串，都不例外。`mime.startswith("audio/")` 这类分支读的是原值，不受渲染影响。
  import 顺序：`file_safety` 在 `inline_field` 之前。

## 2026-08-03 — `persist_attachment_bytes`:"bytes → Attachment" 的单一居所

store + per-day index + audio/* Whisper STT(never-raise)+ Attachment
构造,原本内联在 channel_trigger_base._persist_attachment 里;抽到这里
后由两个调用方共享:trigger 的 `_persist_attachment`(下载→persist)与
managed ingress 的附件转换器(平台已落盘→本地读→persist)。STT 的
TranscriptionService 仍是惰性导入,存储层 import 期不依赖
agent_framework。

# attachment_storage.py

## Why it exists

Workspace-scoped storage for chat attachments — the **only** layer that
knows on-disk paths. Three jobs:

1. `store_uploaded_attachment` writes the bytes and updates the daily
   index (called by the upload route)
2. `resolve_attachment_path` translates a `file_id` to an absolute path
   with a sandbox check (called by the raw-download route, marker
   synthesis, and system-prompt formatting)
3. `format_attachments_for_system_prompt` renders the current-turn
   attachment list as a Markdown block listing names + types + paths
   (called by `CommonToolsModule.contribute_instructions` to inject "the user
   uploaded these files NOW" into the system prompt)

Centralizing path knowledge here keeps the seam clean: swap this file
for an S3 / object-store backend and nothing else moves.

## Upstream / Downstream

Upstream callers:
- `backend.routes.agents.attachments.upload_attachment` writes via
  `store_uploaded_attachment`
- `backend.routes.agents.attachments.get_attachment_raw` reads via
  `resolve_attachment_path`
- `narranexus.platform.schema.attachment_schema.Attachment.synthesize_marker`
  reads via `resolve_attachment_path`
- `narranexus_plugins.common_tools_module.common_tools_module
  .CommonToolsModule.contribute_instructions` reads via
  `format_attachments_for_system_prompt`

Downstream:
- `narranexus.platform.utils.file_safety` for `sanitize_filename` /
  `ensure_within_directory` — re-used so all upload paths share the
  same sandbox semantics
- `narranexus.platform.settings` for `base_working_path`

## Design decisions

**No custom MCP tool layer**. The only thing the agent needs is the
absolute path, which it hands to the built-in `Read` tool. So this util
exposes path resolution; nothing here turns bytes into base64. That
work happens inside the Anthropic SDK's Read primitive.

**Sidecar `_index.json` instead of a SQL `instance_attachments` table.**
Zero schema changes, identical behavior on SQLite and MySQL. Lookup is
bounded — at most two daily directory reads (today + yesterday for
midnight-crossing sessions). When upload volume warrants a real index
we'll migrate; today's workload doesn't.

**`file_id` is the on-disk filename stem.** This keeps resolution
trivial — given a `file_id` we know its filename without consulting
any external state. The original user filename is stored alongside in
the index for display only.

**Cross-tenant isolation is structural, not policy.**
`get_workspace_path(agent_id, user_id)` produces a directory that is
unreachable from any other agent_id/user_id pair, and the resolver
re-validates with `ensure_within_directory` even if the index is
corrupt.

## Gotchas

- `_RESOLVER_LOOKBACK_DAYS = 2` limits how far back the per-day index
  can be queried. A session that pauses for 3+ days and references an
  old attachment will fall through to the directory scan — bounded but
  slower. Bump the lookback if multi-day sessions become common.
- `format_attachments_for_system_prompt` accepts the `category` field
  as either a string (WS payload, JSON memory) or an
  AttachmentCategory enum (Pydantic model_dump default). Don't tighten
  this — the dual shape is real and we tolerate both.
- `format_attachments_for_system_prompt` also surfaces the `transcript`
  field inline when present (audio uploads). Without this, the agent
  sees only the file path and tries to "play" the audio; with the
  inline transcript the LLM reads the spoken content directly. Mirrors
  the same logic in `Attachment.synthesize_marker` for chat-history
  replay; keep the two in sync.
- For audio uploads with NO transcript, the formatter writes
  `transcript=<unavailable: ...>` instead of staying silent. The agent
  needs to know **why** the transcript is missing — without that hint
  the LLM falls back to "I can't listen to audio" instead of telling
  the user to add an OpenAI provider. Non-audio files (image / PDF /
  text) intentionally do not get this hint; only `mime.startswith("audio/")`
  triggers it.
- `_write_index` writes to a `.tmp` and `os.replace`s — atomic on POSIX,
  but if the process is killed between successive saves of the same
  index, you may lose the most recent entry. The bytes are still on
  disk; only the metadata is missing. The fallback dir-scan recovery
  path in `resolve_attachment_path` catches this case.

## New-joiner traps

- Don't call `generate_file_id()` outside this module. It is the
  declared truth for the file_id format and `is_valid_file_id()` is
  its paired validator — keep them together.
- `get_workspace_path` deliberately mirrors the layout used by
  `backend/routes/agents/files.py`. If that route's path scheme
  changes, this function must change in lockstep.
