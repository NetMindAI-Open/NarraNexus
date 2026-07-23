---
code_file: backend/routes/transcription_public.py
last_verified: 2026-07-21
stub: false
---

## 2026-07-21 — shared-area fallback for team voice memos

`_resolve_path_for_variant` now falls back to
`message_bus.attachments.resolve_shared_file_by_id(user_id, file_id)` when the agent-scoped
`resolve_attachment_path` misses. Team voice memos are stored in the per-user shared bus
area (not an agent's user_upload_files), so the NetMind STT worker's signed-URL fetch would
404 without this. Still gated by the HMAC token + user_id scoping; `agent_id` may be empty
for team memos. See [[teams]] / [[_bus_attachment_impl]].

# transcription_public.py — JWT-bypassed audio fetch for NetMind

## Why it exists

NetMind's STT worker fetches audio over plain HTTP — there's no JWT
bypass it can use. This route sits at `/api/public/transcription/`
which is exempted from auth_middleware (see
`backend/auth.py::AUTH_EXEMPT_PREFIXES`). The HMAC-signed token IS
the auth.

## Endpoint

- **GET `/api/public/transcription/audio/{token}`**

  - 200: streams audio bytes via FileResponse
  - 401: signature mismatch / malformed token (TokenInvalid)
  - 410: signature valid but timestamp expired (TokenExpired)
  - 404: token decoded but file missing on disk (orphan or
    non-existent transcoded variant)

## Variant routing

Tokens carry a `variant` field:

- `original` → serve the file `resolve_attachment_path` returns
  (uploaded mp3/wav/flac/ogg goes straight through)
- `mp3` → serve the cached transcoded sibling at
  `original.with_suffix(".mp3")` — written by NetMind backend before
  minting the URL when the upload is webm/m4a/mp4

If the variant file doesn't exist we return 404 — never silently fall
back to the original. NetMind would then try to decode webm and fail
with a confusing "Soundfile malformed" error; better to 404 the
fetch and let the backend re-mint after a successful transcode (or
return None and let resolver try the next candidate).

## Gotchas

- This route's prefix `/api/public/` is registered in
  `AUTH_EXEMPT_PREFIXES` to bypass JWT. Don't move the route under
  another prefix — the bypass is structural to the path.
- `Content-Disposition: filename=audio.<ext>` deliberately doesn't
  leak our internal `file_id` naming. NetMind doesn't care about
  the filename; the obfuscation costs nothing.
- The token itself is in the URL path, not a query param. Path tokens
  show up in access logs anyway, and putting them in the path makes
  the rate-limit / CDN cache layer treat each token as a distinct
  resource — desirable.
