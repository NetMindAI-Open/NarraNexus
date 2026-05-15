---
code_file: src/xyz_agent_context/agent_framework/transcription/url_signer.py
last_verified: 2026-05-07
stub: false
---

# url_signer.py — HMAC-signed audio URLs for NetMind

## Why it exists

NetMind's STT worker fetches audio from a public URL — there's no
JWT bypass we can teach NetMind to use. Rather than provisioning S3
to host audio, we expose a single JWT-bypassed FastAPI route that
validates a short-TTL HMAC token. NetMind backend mints, route
verifies; the token IS the auth.

## Design decisions

- **HMAC-SHA256, not JWT.** Tokens are read by exactly two pieces of
  our own code, and they don't need claims a JWT verifier elsewhere
  would parse. JWT would import another library and a more permissive
  validation surface; HMAC is 30 lines.
- **Variant field.** `original` vs `mp3` — lets the same token shape
  serve either the raw upload (when NetMind can decode it directly)
  or the cached transcoded sibling. Adding new variants is a verify()
  enum check; the route dispatches.
- **TTL = 10 min default.** NetMind probe data: 18s typical
  end-to-end, leaving ~30× headroom for queue spikes and one retry.
  Smaller TTLs risk killing legitimate slow runs; larger ones invite
  replay.
- **Cloud mode refuses derived secrets.** If
  `settings.transcription_hmac_secret` is unset and we're in cloud,
  `_secret()` raises RuntimeError — caller (NetMind backend) catches
  and degrades to `None`. We do NOT silently fall through to the
  admin_secret_key in production: signing audio URLs with a
  guessable / shared secret is the kind of thing that gets you on
  HN front page.

## Upstream

- NetMind backend (`backends/netmind.py`) — the only minter.
- `backend/routes/transcription_public.py::fetch_audio` — the verifier.

## Gotchas

- Tokens contain `agent_id` and `user_id` to scope every audio fetch
  to a sandbox check via `resolve_attachment_path`. Don't drop those
  fields when adding new variants.
- `_b64url_decode` re-pads with `=` before decoding. `mint()` strips
  it for cleaner URLs; if you skip the re-pad on verify, base64 will
  raise on every round-trip.
