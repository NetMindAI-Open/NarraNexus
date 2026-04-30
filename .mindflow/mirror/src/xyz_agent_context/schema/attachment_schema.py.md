---
code_file: src/xyz_agent_context/schema/attachment_schema.py
last_verified: 2026-04-29
stub: false
---

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
`xyz_agent_context.utils.attachment_storage`.

## Upstream / Downstream

Producers:
- `backend/routes/agents_attachments.py::upload_attachment` builds an
  Attachment after sniffing MIME and storing the bytes
- `frontend/src/components/chat/ChatPanel.tsx` accumulates Attachments
  in `pendingAttachments` state and sends them with the WS payload

Consumers:
- `backend/routes/websocket.py::AgentRunRequest` accepts a list of
  Attachment dicts via `attachments`
- `xyz_agent_context.module.chat_module.chat_module` persists them on
  the user message in JSON memory and synthesizes natural-language
  markers (with absolute paths) for chat_history
- `xyz_agent_context.module.common_tools_module.common_tools_module`
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

**`caption` and `transcript` are reserved fields, not implemented.** They
exist now so we don't have to change the JSON shape stored in chat
history later — Phase 2 (vision-LLM caption) and the future audio/video
transcription module just populate them.

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
