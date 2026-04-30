---
code_file: backend/routes/agents_attachments.py
last_verified: 2026-04-29
stub: false
---

# agents_attachments.py

## Why it exists

HTTP boundary for the chat-attachment lifecycle: a multipart upload that
returns a server-issued `file_id`, plus a raw-bytes endpoint the
frontend uses to render image thumbnails inline. Kept separate from
`agents_files.py` because chat attachments have a different storage
shape (date-partitioned subdirs + sidecar index) and a different
access pattern (referenced by `file_id`, not browsed by name).

## Upstream / Downstream

Upstream:
- Frontend `ChatPanel.tsx` calls `POST /agents/{aid}/attachments` for
  every dropped/picked file before sending the chat message
- Frontend `MessageBubble.tsx` builds `<img src=>` URLs pointing at
  `GET /agents/{aid}/attachments/{file_id}/raw`

Downstream:
- `xyz_agent_context.utils.attachment_storage.store_uploaded_attachment`
  writes the file and updates the daily index
- `xyz_agent_context.utils.attachment_storage.resolve_attachment_path`
  re-resolves on `/raw` requests with the workspace sandbox check
- `xyz_agent_context.schema.attachment_schema.derive_category_from_mime`
  classifies the upload so the frontend can render an icon vs a thumbnail

Mounted under `/api/agents` via `backend/routes/agents.py`.

## Design decisions

**Server-side MIME sniffing, not client-trusted Content-Type.** The
client value is user-controlled and easy to spoof. We try
`python-magic` first (real content sniffing), fall back to extension
guessing, and only use the client-supplied type if both fail.

**Single-file upload, no multi-file form.** Frontend uploads files
sequentially so each gets its own `file_id` round-trip; this keeps
error handling simple (one failure ≠ all failures) and lets the UI
show per-file progress without server complexity.

**`/raw` returns a 404 JSONResponse on every error path** — invalid
file_id, missing file, sandbox violation. We deliberately do not leak
which one occurred; from the caller's perspective they're all "this
file_id is not yours / not real."

## Gotchas

- The `attachmentRawUrl` helper in the frontend hardcodes the same
  path shape this file exposes. Changing the URL here without updating
  `frontend/src/lib/api.ts` will silently break image previews.
- `backend_settings.max_upload_bytes` governs storage size; the
  separate 5 MB Vision-API ceiling is enforced at MCP read time
  (`image_loader.py`). They do not overlap on purpose — we accept
  uploads larger than Vision can read so the user still sees the file
  chip; only image preview / agent vision fails for oversize.

## New-joiner traps

- This route does **not** persist anything in the chat message. The
  WS `AgentRunRequest.attachments` field is what links a `file_id` to
  a turn. Uploading without sending leaves orphan files (cleanup is
  Phase 2 work).
- Authentication is handled by FastAPI middleware, not in this file —
  same pattern as `agents_files.py`.
