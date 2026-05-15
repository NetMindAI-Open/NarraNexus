---
code_file: backend/routes/transcription.py
last_verified: 2026-05-07
stub: false
---

# transcription.py — voice-input availability pre-flight

## Why it exists

`AudioRecorder` on the frontend pre-checks whether the current user
has any transcription-capable provider before letting the mic button
start MediaRecorder. Without this, recording first and only
discovering "no provider configured" via the post-upload banner is a
worse UX than asking up front.

## Endpoints

- **GET `/api/transcription/availability`** — JWT-protected (cloud mode)
  / query-param user_id (local mode), returns
  `{available: bool, reason: str}`. The `reason` is a frontend-readable
  short code (`has_openai`, `has_netmind`, `has_other`,
  `system_free_tier`, `none`) that lets the click-time dialog vary its
  copy.

## Gotchas

- This route is **JWT-protected**. The companion route
  `transcription_public.py` is the one that's auth-bypassed — don't
  confuse them.
- The `_resolve_user_id` helper supports BOTH JWT-derived state.user_id
  and a `?user_id=` query param. Cloud-mode middleware sets the
  former; local mode has only one trusted user and we accept the
  query param to match the existing dashboard endpoints.
- Returns `available=true, reason="unknown"` if the service raises —
  better to false-positive once and let the upload-time banner take
  over than to block voice input on a probe network blip.
