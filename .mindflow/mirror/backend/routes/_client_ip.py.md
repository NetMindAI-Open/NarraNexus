---
code_file: backend/routes/_client_ip.py
last_verified: 2026-10-28
stub: false
---

# routes/_client_ip.py — the one answer to "who is calling"

## Intent

Every per-IP decision in the backend has to agree on what "the client's IP" is,
or a limiter silently measures something other than the caller. This module is
that single answer, and it exists because there were briefly two: the auth
funnel counted X-Forwarded-For from the right, and the anonymous channel
webhook (`backend/routes/channels/generic.py`) read `request.client.host`.

`request.client.host` is the wrong one **in production, always**: uvicorn is
started without `--proxy-headers` (the deploy repo's
`stacks/narranexus-app/backend-bind.sh`), so behind the stack's nginx the socket
peer is the same container address for every cloud request. A limiter keyed on
it is one shared bucket — it provides no per-attacker limiting at all, and one
abuser spending the budget 429s every legitimate caller at once. That failure
reads as protection in review, which is why it must not be reachable by copying
the obvious attribute.

## The one assumption

`TRUSTED_PROXY_HOPS` — the number of proxy hops in front of this backend, not
any proxy's XFF semantics. Both hops in this chain APPEND, but the reasoning
holds for overwrite too: whatever the edge contributes sits the same distance
from the RIGHT, while the leftmost entry is caller-written and forgeable.
Today: client → ops-caddy → frontend nginx → backend = 2.

`FUNNEL_TRUSTED_PROXY_HOPS` overrides it. The deploy repo's `caddy/local/*.caddy`
per-env routes are outside this repo's sight, so **adding or removing a hop
there must bump this** — get it wrong and per-IP collapses into a second global
bucket, silently. Clamped to ≥ 1: at 0, `parts[-0] == parts[0]` is the
caller-written entry and `len(parts) >= 0` is always true, so the short-chain
fallback never fires and an empty header IndexErrors — and 0 is exactly what
someone deploying with no proxy would write. Empty/garbage values fall back to
the default rather than raising at import time (the `executor_reaper`
precedent).

## Gotchas

- A shorter-than-expected chain (local runs, tests, a single forged entry) falls
  back to the socket peer. Never to caller-supplied text.
- Both current callers key in-process sliding windows on the result
  (`SlidingWindowRateLimiter`). That is fine single-host (铁律 #20) as long as
  the key stays behind that seam — do not add a second bespoke counter.

## Upstream / downstream

- Used by: `backend/routes/auth.py` (funnel report limiter; signup,
  send-code and netmind-login per-IP buckets),
  `backend/routes/channels/generic.py` (anonymous webhook IP limiter).
- Tests: `tests/backend/test_auth_funnel_observability.py` (hop parsing and
  counting-from-the-right), `tests/backend/test_channel_webhook_middleware.py`
  (`X-Forwarded-For` giving two callers independent buckets).
