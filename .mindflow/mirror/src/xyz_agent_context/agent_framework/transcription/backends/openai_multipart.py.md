---
code_file: src/xyz_agent_context/agent_framework/transcription/backends/openai_multipart.py
last_verified: 2026-05-07
stub: false
---

# openai_multipart.py — OpenAI /audio/transcriptions backend

## Why it exists separately from netmind.py

Two backends, two protocols, two retry/timeout profiles. Not three
shared classes pretending to abstract over one common interface.
This file is the lift-and-clean of the 2026-05-02-shipped
`utils/audio_transcription._call_whisper`, with the resolver / credential
chain factored out into its own module.

## Why no transcoding

OpenAI Whisper accepts webm/opus, m4a, ogg, mp3, wav, flac natively.
The browser-recorded webm goes straight to `/audio/transcriptions`
without modification. The transcoding cost is borne only by NetMind.

## Retry policy

Two attempts max. 429/5xx → retry with linear backoff. Anything else
(401/403/404/422) is a permanent contract failure — log once and bail.
The retry is on the same file, with the file handle re-opened for each
attempt; reusing the fp posts 0 bytes and is the test-locked footgun
this implementation has hit before.

## Gotchas

- 25 MB hard cap is OpenAI's, not ours. Backend cap (`max_upload_bytes`,
  default 50 MB) accepts larger files because images / docs aren't
  Whisper-bound — but we still need to refuse oversize audio here
  rather than wasting the multipart upload to OpenAI for a guaranteed 4xx.
- `_MIME_BY_EXT` has tighter coverage than the loose libmagic sniff —
  Whisper rejects unknown MIME on the upload, so we want the explicit map.
- `del file_id, agent_id, user_id` at the top is intentional: makes
  unused-parameter linters happy without removing them from the
  signature (the ABC requires them).
