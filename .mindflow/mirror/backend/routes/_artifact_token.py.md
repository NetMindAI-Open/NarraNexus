---
code_file: backend/routes/_artifact_token.py
last_verified: 2026-05-14
stub: false
---

# _artifact_token.py — HMAC view tokens for artifact raw serving

## Why it exists

Multi-file HTML artifacts need iframe `src=` to point at a real URL (so the
entry document's relative references resolve), but native iframe loads can't
attach Authorization headers. Cloud-mode JWT auth therefore can't gate the raw
content — the auth must live IN the URL.

This module mints + verifies HMAC-SHA256 tokens that carry `(agent_id,
artifact_id, exp)`. The JWT-authed
`GET /api/agents/{aid}/artifacts/{aid}/view-token` endpoint mints them; the
public route in `artifacts_public.py` verifies them.

## Token format

Mirrors `agent_framework/transcription/url_signer.py` (same wire shape so the
operational mental model is one tool):

    base64url(payload_json) + "." + base64url(hmac_sha256_digest)

`payload_json = {"agent_id": str, "artifact_id": str, "exp": int}` — sorted
keys, no whitespace, so the byte sequence signed by `mint` is the exact byte
sequence `verify` recomputes the digest over.

## Signing secret

`settings.transcription_hmac_secret` is the explicit cloud-mode secret. The
fallback chain (admin_secret_key in local mode; RuntimeError if neither set
in cloud mode) mirrors `url_signer._secret`. Sharing the same secret is
deliberate — it's a deployment-level signing key, not a feature-specific one.

## TTL

`DEFAULT_TTL_SECONDS = 2 * 60 * 60` (2 hours). Generous: an artifact tab can
sit open for a while; sub-resources may load lazily long after the iframe
first rendered. An expired token returns 410 from the public route, which the
frontend handles by re-minting transparently.

## Errors

`TokenInvalid` → 401 (bad format, base64 fail, signature mismatch, missing
fields). `TokenExpired` → 410 (signature valid, `exp` past). Both inherit
from `TokenError` which carries `http_status` so the route handler maps
errors with no branching.

## Gotchas

- `mint` and `verify` MUST use identical payload serialisation (sorted keys,
  no whitespace). Drift between the two breaks every previously-issued token.
- The constant-time `hmac.compare_digest` is required — never use `==` on
  HMAC digests.
- This module is a peer-private helper inside `backend/routes/` (leading
  underscore). Import it as `from backend.routes import _artifact_token`,
  not `from backend.routes._artifact_token import ...`, to keep call sites
  searchable as "`_artifact_token.mint` / `_artifact_token.verify`".
