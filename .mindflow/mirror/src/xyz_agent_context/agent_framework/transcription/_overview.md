---
last_verified: 2026-05-07
stub: false
---

# transcription/ — provider abstraction for audio → text

## Why it exists

Phase-1 multimodal-audio shipped a single hard-coded Whisper client at
`utils/audio_transcription.py` that only spoke OpenAI's multipart contract.
NetMind's `/v1/generation` endpoint with JSON+`audio_url` was rejected at
the resolver level, OpenRouter's JSON+base64 was likewise out. Adding a
second backend would have meant either bolting `if/else` branches into
the single file or shipping a parallel utility — neither composes well
beyond two backends.

This subpackage is the abstraction the user asked for in the
2026-05-07 conversation: "把 STT 调用也抽象出来，类似 LLM 调用". One
service entry point (`TranscriptionService`), one resolver, multiple
backend implementations sharing a common base interface, and an HMAC
URL signer for backends that need public-URL hosting.

## Structure

```
transcription/
├── __init__.py        — re-exports TranscriptionService + supporting types
├── service.py         — singleton facade; walks resolver candidates
├── credential.py      — TranscriptionCredential dataclass + backend kind enum
├── resolver.py        — 5-tier ordered fallback (user OpenAI → user
│                          NetMind → user other → settings.openai (local)
│                          → system-default NetMind cloud free tier)
├── url_signer.py      — HMAC-SHA256 signed audio URLs for NetMind
└── backends/
    ├── base.py        — TranscriptionBackend ABC + per-backend timeout matrix
    ├── openai_multipart.py  — OpenAI Whisper /audio/transcriptions
    └── netmind.py     — NetMind /v1/generation submit+poll, lazy ffmpeg transcode
```

## Design intent (frozen by spec 2026-05-07)

- **Capability is derived, never user-visible.** The data model has no
  "transcription provider" concept — the resolver introspects existing
  `ProviderConfig` rows and picks a backend based on `base_url`.
- **Never raise.** Any failure (network, malformed audio, timeout,
  ffmpeg missing, signing-secret unset) returns `None`. Upload route's
  contract requires it.
- **NetMind requires public URL hosting.** url_signer + the
  `/api/public/transcription/audio/{token}` route together replace
  what would otherwise be an S3 dependency.
- **NetMind doesn't decode webm/m4a/mp4.** Browser MediaRecorder
  output is incompatible with NetMind's soundfile decoder. NetMind
  backend lazily transcodes via ffmpeg, caching `{file_id}.mp3`
  next to the original.
- **Quota bypass for cloud free tier.** Transcription does not consult
  `cost_tracker` at all; `is_system_free_tier=True` on the credential
  is documentation, not gating.

## Upstream

- `backend/routes/agents_attachments.py::upload_attachment` —
  the only production caller. Imports `TranscriptionService.instance()`
  for both `is_available(user_id)` and `transcribe(...)`.
- `backend/routes/transcription.py::availability` — frontend mic-button
  pre-flight; calls `service.availability_reason(user_id)`.
- `backend/routes/transcription_public.py::fetch_audio` — NetMind's
  worker fetch path; consumes `url_signer.verify(token)`.

## Reference

Design doc: `reference/self_notebook/specs/2026-05-07-transcription-provider-abstraction-design.md`.
