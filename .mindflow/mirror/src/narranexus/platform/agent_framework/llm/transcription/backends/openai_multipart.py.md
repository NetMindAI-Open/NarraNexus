---
code_file: src/narranexus/platform/agent_framework/llm/transcription/backends/openai_multipart.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 (II) — the cloud check moved into the shared wrapper

PR#418 review I1: the inline `if is_cloud_mode(): assert_public_http_url(url)`
now calls `url_safety.assert_public_http_url_in_cloud(url)`, the single place
that decides "cloud enforces, local allows". This backend keeps only its own
failure contract — a refusal still returns `None` (the service tries the next
candidate) and logs the reason — and it still gates the FULL assembled
`.../audio/transcriptions` URL, not the bare base (do not "unify" it to the
base: that would change which scheme/port `urlparse` judges).

## 2026-10-28 — Egress gate before posting (cloud)

The resolver can hand this backend a custom openai card's `base_url` — user-
controlled — and a 200 body becomes the transcript that lands in the agent's
context, so this was a readable SSRF into the cluster. In cloud mode the full
`.../audio/transcriptions` URL now passes `url_safety.assert_public_http_url`
before anything is sent; any refusal (internal address, DNS failure, malformed)
returns `None` = "this candidate can't transcribe", so the service falls through
to the next candidate or reports transcription unavailable — never a 500.

Every credential is gated, not just user-sourced ones: no candidate this backend
serves legitimately points inside our network (the free-tier card is routed to
the gateway backend; `settings.openai` is api.openai.com). Local mode skips the
gate — a self-hosted whisper.cpp on localhost is a valid desktop setup.
`follow_redirects=False` is now explicit on the client. Not done: IP pinning
(see the url_safety mirror).

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
