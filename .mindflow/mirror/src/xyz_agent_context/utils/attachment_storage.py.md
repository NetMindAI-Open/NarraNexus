---
code_file: src/xyz_agent_context/utils/attachment_storage.py
last_verified: 2026-05-04
stub: false
---

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
   (called by `CommonToolsModule.get_instructions` to inject "the user
   uploaded these files NOW" into the system prompt)

Centralizing path knowledge here keeps the seam clean: swap this file
for an S3 / object-store backend and nothing else moves.

## Upstream / Downstream

Upstream callers:
- `backend.routes.agents_attachments.upload_attachment` writes via
  `store_uploaded_attachment`
- `backend.routes.agents_attachments.get_attachment_raw` reads via
  `resolve_attachment_path`
- `xyz_agent_context.schema.attachment_schema.Attachment.synthesize_marker`
  reads via `resolve_attachment_path`
- `xyz_agent_context.module.common_tools_module.common_tools_module
  .CommonToolsModule.get_instructions` reads via
  `format_attachments_for_system_prompt`

Downstream:
- `xyz_agent_context.utils.file_safety` for `sanitize_filename` /
  `ensure_within_directory` — re-used so all upload paths share the
  same sandbox semantics
- `xyz_agent_context.settings` for `base_working_path`

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
  `backend/routes/agents_files.py`. If that route's path scheme
  changes, this function must change in lockstep.
