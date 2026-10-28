---
code_file: src/narranexus/platform/schema/attachment_schema.py
last_verified: 2026-09-13
stub: false
---

## 2026-09-13（PR#401 review 第九轮）— path 改为无损编码、`category_value`、`from=` 口径

- **不变量更新：每个值都编码；`path` 用精确（无损）字面量 `exact_literal`；只有语法固定字面量裸露。**
  上一轮 `path` 走了 `inline_literal`，会折叠空白/剥首尾/换反引号，部署方自选的工作目录（`/data/nexus  ws`、
  尾随空格、反引号）下每个附件路径都打不开。现在 `json.loads(path)` 逐字节等于磁盘路径，仍然一行、仍不可伪造。
  `name` / `mime` / `kind` / `from` / `transcript` 是展示文本，继续走 `inline_literal`（transcript 折叠断行是必要的）。
- `category_value(value)`：`AttachmentCategory` → `.value`，其他原样（`isinstance` 判断，不做 `hasattr` 通用探测）。
  `file_marker` 内部对 `kind` 统一处理，调用方传原值（`synthesize_marker` 不再 `.value`）；
  `attachment_storage.format_attachments_for_system_prompt` 的 `type=` 用同一函数。原因：3.11+ 下
  `str(AttachmentCategory.IMAGE)` 是 `"AttachmentCategory.IMAGE"`。
- `from=` 一律是加引号的**标签**，即使 DM 路径传的是 agent id：DM prompt 的裸 `From:` 行才是 agent 抄句柄的来源。
  这是对「句柄不加引号」规则的明确例外（一个字段只能有一种编码），写在 `file_marker` docstring 里。

## 2026-09-13（PR#401 review 🟡2 → 第八轮）— `file_marker`：唯一渲染函数，所有值一律编码

用户上传 marker（`Attachment.synthesize_marker`）与总线附件 marker（`_bus_attachment_impl.build_bus_markers`）
原来是两份拷贝且已分叉，现在都调模块级 `file_marker(label, *, name, path, mime, kind, sender=None, transcript=None)`：
`[<label>: name="…", path="…", mime="…", kind="…"[, from="…"][, transcript="…"] — use Read tool to view]`。

**不变量：marker / 附件列表行里每个值都编码（第九轮起 `path` 用无损的 `exact_literal`，其余 `inline_literal`），只有语法自己的固定字面量裸露。**
裸露的只有：`label`（`FILE_MARKER_LABELS` 二选一：`User uploaded file` / `Shared file`，传别的直接 `ValueError`）、
字段键名、路径解析失败时的 `FILE_MARKER_UNAVAILABLE_PATH`（`<unavailable>`）、结尾 `FILE_MARKER_TAIL`。
不再逐字段判断「是不是平台构造」——上一轮就是把 `mime` 判成平台构造而漏掉：`sniff_mime_type` 第三层会把
外部发送方声明的 Content-Type 原样返回。所以 `path`、`mime`、`kind`、发送者一律编码：
- 旧 head 里嵌的 `kind`（`User uploaded image`）与发送者（`Shared file from agent X`）挪成 `kind=` / `from=` 字段，
  head 变成固定字面量；`sender` 传**原始**名字/句柄，由 `file_marker` 编码（团队房传 `_sender_raw`，DM 传 `from_agent`）。
- `path` 是 JSON 字面量（第九轮起用 `exact_literal`，逐字节回解，不做任何折叠，见上段）；
  落盘后缀清洗（`on_disk_suffix`）仍在，但不再是这一行安全性的前提（历史文件的旧后缀也被编码挡住）。
- `mime_type` 字段描述改为「最后一层会回落到客户端声明值，按不可信文本处理」。
- 对 `platform.utils.inline_field` 的 import 放在 `file_marker` 函数体内（与 `resolve_attachment_path` 同形）：
  schema 在 import 期不拉 `platform.utils`（其包 `__init__` 会起 db 层）。lint-imports 7 条契约均 KEPT。

同类渲染器扫描（`git grep -n "use Read tool to view\|User uploaded\|mime={\|transcript="`，排除 tests/reference）：
- 渲染器只有两处：`file_marker`（本文件）与 `attachment_storage.format_attachments_for_system_prompt`（当前轮
  附件列表，另一种行语法，同一编码器、同一不变量）。
- 消费 marker 的入口都经 `markers_from_dicts` / `build_bus_markers`，不自己拼：`chat_module`、
  `context_runtime.build_input_for_framework`、`narramessenger_context_builder`、`message_bus_trigger`（团队房 + DM）。
- 把 marker 形状写进 agent 指令的三处 docstring 已同步：slack_module、telegram_module、common_tools_module。
- 只提到 "use Read tool to view" 字样、不含形状的：`matrix_trigger.py:2853`、`lark_trigger.py:531`，无需改。
- 仓内没有把 marker 解析回结构的消费方（前端只渲染附件 dict，不读 marker 文本）。

## 2026-07-09 — `Attachment.markers_from_dicts` staticmethod added

Single seam for "list of Attachment-shaped dicts → newline-joined Read-tool markers". Replaces the three private copies (`chat_module._synthesize_attachment_markers`, ad-hoc loops in prior WS route drafts and prior channel context builder drafts) with one implementation on the schema. Malformed entries produce a WARNING (`[Attachment.markers_from_dicts] skipping malformed attachment dict: <type>: <msg>`) — silent drops would recreate the "agent claims no file received" incident the 2026-07-09 fix addresses (see [[context_runtime.py]] for the current-turn callsite; [[chat_module.py]] for the history callsite).

# attachment_schema.py

## Why it exists

The Pydantic shape that travels from frontend upload → WebSocket payload →
JSON memory inside `instance_json_format_memory_chat` → marker text in
chat_history. It is the single source of truth for what an "attachment"
means in the system, so the WS schema, ChatModule's hook code,
CommonToolsModule's dynamic instruction, and the React types all agree
on the same field set.

The model intentionally carries only metadata + the `file_id` reference;
binary content lives on disk under the agent workspace via
`narranexus.platform.utils.attachment_storage`.

## Upstream / Downstream

Producers:
- `backend/routes/agents/attachments.py::upload_attachment` builds an
  Attachment after sniffing MIME and storing the bytes
- `frontend/src/components/chat/ChatPanel.tsx` accumulates Attachments
  in `pendingAttachments` state and sends them with the WS payload

Consumers:
- `backend/routes/websocket.py::AgentRunRequest` accepts a list of
  Attachment dicts via `attachments`
- `narranexus_plugins.chat_module.chat_module` persists them on
  the user message in JSON memory and synthesizes natural-language
  markers (with absolute paths) for chat_history
- `narranexus_plugins.common_tools_module.common_tools_module`
  injects the same paths into a system-prompt block for the current turn
- `frontend/src/components/chat/MessageBubble.tsx` renders thumbnails
  for `category=image`, file chips otherwise

## Design decisions

**Marker carries an absolute path, not a tool name.** The agent's
built-in `Read` tool (Anthropic SDK) is multimodal and natively returns
image / PDF / text content blocks — so we point the agent at a path it
can hand straight to Read. No custom MCP tool, no per-Module instance
to manage, no extra port. This collapses what was once an
`AttachmentModule` into a one-line marker.

**Path resolution lives at marker-synthesis time, not upload time.** A
file might be deleted between upload and read, and the marker should
say `<unavailable>` in that case rather than baking in a stale path.
`synthesize_marker(agent_id, user_id)` re-resolves through
`attachment_storage.resolve_attachment_path` every time chat_history is
built.

**Category derivation lives in the schema, not at the call sites.**
`derive_category_from_mime` keeps frontend and backend in lockstep: a
new mime type only needs to be classified once and every layer benefits.

**`transcript` is now actively populated for audio uploads.** Set by
`backend/routes/agents/attachments.py` via
`narranexus.platform.utils.audio_transcription.transcribe_audio` when the
upload's MIME starts with `audio/` AND the user has an OpenAI-protocol
provider configured. `synthesize_marker` checks this field and, when
present, appends `transcript=<text>` to the marker so the agent reads
the spoken content without a separate Read step. `caption` remains a
reserved field — kept on the model so the JSON memory shape doesn't
need a migration when vision-LLM caption synthesis (Phase 2 of vision
support) lands.

## Gotchas

- `FILE_ID_REGEX` is enforced everywhere a file_id crosses a trust
  boundary: upload, raw download, path resolution. Don't relax it
  without revisiting all three.
- `SUPPORTED_IMAGE_MIME_TYPES` is now an informational constant — the
  agent's built-in Read tool decides what it can render. We keep it for
  thumbnail-rendering decisions and future Phase-2 caption synthesis.
- `synthesize_marker` requires `agent_id` and `user_id` because path
  resolution is workspace-scoped. The AI cannot guess these — only the
  ChatModule hook (which has them on `self`) and CommonToolsModule (ditto)
  call it.

## New-joiner traps

- The model accepts `category` as a free string in some serialization
  paths (it's a `str` enum); always go through `AttachmentCategory(value)`
  when constructing programmatically to catch typos.
- Do NOT add fields here that name a specific module (e.g.
  `read_tool_url`, `mcp_endpoint`). The whole point of this redesign is
  that attachments are tool-agnostic — they're just paths the agent's
  built-in primitives can consume.
