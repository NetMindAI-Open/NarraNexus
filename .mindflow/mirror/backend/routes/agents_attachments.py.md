---
code_file: backend/routes/agents_attachments.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — MIME sniffing moved to the shared utils helper

The local ``_sniff_mime_type`` + ``_audio_video_container_override`` pair
moved to [[mime_sniff]] so all three upload/ingest paths (this route, IM
channels, team-chat uploads) classify identically. One behavior delta: a
libmagic ``application/octet-stream`` verdict now falls through to the
extension guess instead of winning outright, so extension-typed text formats
(``.md``, ``.csv``) get their real MIME. The container override semantics are
unchanged.

# agents_attachments.py

## Why it exists

HTTP boundary for the chat-attachment lifecycle: a multipart upload that
returns a server-issued `file_id`, plus a raw-bytes endpoint the
frontend uses to render image thumbnails inline. Kept separate from
`agents_files.py` because chat attachments have a different storage
shape (date-partitioned subdirs + sidecar index) and a different
access pattern (referenced by `file_id`, not browsed by name).

Whisper transcription runs for **every** `audio/*` upload regardless
of how the user produced it — the agent must always receive the
spoken content via the system-prompt attachment marker, whether the
clip came from in-browser dictation or from a file the user attached.
The route's `source` query parameter is purely a frontend-render
hint, normalised on the way out and echoed back so the persisted
attachment dict carries it through chat history reload:

- `source=recording` — the in-browser AudioRecorder produced a voice
  memo. Frontend renders `VoiceTranscript` (transcript text in place
  of the message bubble).
- omitted / `source=upload` / anything else — Paperclip / drag-drop /
  paste. Frontend renders an ordinary file chip; the transcript still
  reaches the agent via the system prompt but is not surfaced in the
  UI (the user attached a file, they didn't dictate, so showing the
  transcript would be confusing).

Transcription routes through the same OpenAI-protocol provider system
that powers chat (`UserProviderService` → `SystemProviderService` →
`settings.openai_api_key`), so any user with a compatible provider
gets transcription "for free". Failures never break the upload — they
degrade to `transcript=null` and the response also exposes
`transcription_available` so the frontend can surface a "voice
unavailable" message specifically on the recording path.

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
- `xyz_agent_context.utils.audio_transcription.transcribe_audio` /
  `is_transcription_available` — called only on `audio/*` uploads, with
  the request's `user_id` so per-user provider lookup works

Mounted under `/api/agents` via `backend/routes/agents.py`.

## Design decisions

**Server-side MIME sniffing, not client-trusted Content-Type.** The
client value is user-controlled and easy to spoof. We try
`python-magic` first (real content sniffing), fall back to extension
guessing, and only use the client-supplied type if both fail.

**Audio-vs-video container disambiguation.** WebM, Ogg, and MP4 are
multimedia containers — the file header is identical for audio-only
and audio+video streams, and libmagic looks at the header alone, so
it always reports `video/<container>` for these formats. The
in-browser `AudioRecorder` records with `MediaRecorder` into one of
these containers and tags the upload as `audio/<container>` in the
multipart Content-Type. `_audio_video_container_override` consults
the browser claim ONLY when libmagic returned `video/<X>` AND the
browser explicitly declared `audio/<X>` for the same container —
narrow enough to be a safe tiebreaker, wide enough to unblock all
three browser recording paths (Chrome/Firefox webm, Safari mp4,
older Firefox ogg). Misclassification can't escalate: the file
still lands on disk and Whisper silently no-ops on non-audio bytes.

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
