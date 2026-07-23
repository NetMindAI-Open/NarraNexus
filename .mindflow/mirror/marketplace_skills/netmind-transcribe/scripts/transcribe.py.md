---
code_file: marketplace_skills/netmind-transcribe/scripts/transcribe.py
last_verified: 2026-07-21
stub: false
---

# transcribe.py

Stdlib-only whisper call, two routes: http(s) URL → native /v1/generation
submit+poll (works; URL must be publicly reachable by NetMind's workers);
local file → OpenAI-compatible /audio/transcriptions multipart, which is
CURRENTLY BROKEN server-side at NetMind (500 on valid speech audio,
request_ids f001d463… / f350908c… reported 2026-07-21) — failures explain
the URL fallback. Revisit when NetMind fixes the proxy endpoint.
