---
code_file: src/xyz_agent_context/agent_framework/transcription/backends/base.py
last_verified: 2026-05-07
stub: false
---

# backends/base.py — TranscriptionBackend ABC + timeout matrix

## Why it exists

One async method, never-raise. Every concrete backend lives in its
own module under this package and is registered by
`TranscriptionService.__init__`.

## transcribe() signature notes

`file_id`, `agent_id`, `user_id` are keyword-only because most
backends ignore them — the OpenAI multipart one sends bytes
directly. NetMind needs them to mint signed URLs. Forcing them as
kwargs makes that asymmetry explicit at every call site rather than
hiding it in positional drift.

## BACKEND_TIMEOUTS_S

The single source of truth for "how long am I willing to wait per
backend". OpenAI multipart is one round-trip + one retry, fits in 35s.
NetMind probe data showed 18s typical for short audio; 60s gives
~3.3× headroom for queue spikes and the 0.8s polling cadence.

If a third backend lands here (OpenRouter base64, on-device whisper.cpp),
add an entry to this dict. The service uses `.get(kind, 60.0)` so a
missing entry won't crash but will silently apply 60s — which is fine
for unfamiliar backends but worth tightening once the new backend's
behavior is profiled.
