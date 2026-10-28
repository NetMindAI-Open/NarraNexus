"""
@file_name: _client_ip.py
@author: Bin Liang
@date: 2026-09-07
@description: The ONE answer to "what is this request's client IP" behind the deploy stack's proxy chain.

Every per-IP decision in the backend (auth-funnel bucketing, the anonymous
webhook rate limiter) has to agree on this, or the limiters silently measure
something other than the caller. Uvicorn runs WITHOUT ``--proxy-headers``, so
``request.client.host`` is the nginx container's address for every cloud
request: a limiter keyed on it is one shared bucket, and one abuser 429s
everybody. This module is imported by both callers rather than copied, so a
topology change is one edit and not a hunt.
"""
from __future__ import annotations

import os
from typing import Optional

from fastapi import Request

# Counting X-Forwarded-For from the RIGHT depends on exactly ONE fact: the
# number of proxy hops in front of this backend. (Not on any proxy's XFF
# semantics — whether the edge appends to or overwrites a forged header,
# the entry it contributes is the same distance from the right.) Today the
# cloud chain is client -> ops-caddy -> frontend nginx -> backend = 2 hops
# (the DEPLOY repo's docker/nginx.conf — not a file in this repo — carries
# the reverse pointer for topology editors);
# the deploy repo's caddy/local/*.caddy per-env routes are OUTSIDE this
# repo's sight, so anyone adding/removing a hop there (CDN, ALB, an extra
# proxy) MUST bump this — misconfigure it and per-IP silently collapses
# into one shared bucket. Overridable per deployment, no config required.


def _parse_trusted_proxy_hops(raw: Optional[str]) -> int:
    """Clamp to >= 1: hops=0 would make ``parts[-0] == parts[0]`` — the
    CALLER-written entry — and `len(parts) >= 0` is always true, so the
    short-chain fallback would never fire (empty header would even
    IndexError). Empty/garbage values fall back to the default rather
    than blowing up at import time (the executor_reaper precedent)."""
    try:
        return max(1, int(raw or 2))
    except (TypeError, ValueError):
        return 2


TRUSTED_PROXY_HOPS = _parse_trusted_proxy_hops(os.getenv("FUNNEL_TRUSTED_PROXY_HOPS"))


def client_ip(request: Request) -> str:
    """Client IP as seen by the edge proxy: the N-th X-Forwarded-For entry
    from the right (N = TRUSTED_PROXY_HOPS — the ONLY assumption, see its
    comment). A shorter-than-expected chain (local runs, tests, or a
    directly-forged single-entry header) falls back to the socket peer
    rather than trusting caller-supplied text."""
    parts = [
        p.strip()
        for p in (request.headers.get("x-forwarded-for") or "").split(",")
        if p.strip()
    ]
    if len(parts) >= TRUSTED_PROXY_HOPS:
        return parts[-TRUSTED_PROXY_HOPS]
    return request.client.host if request.client else "-"


__all__ = ["TRUSTED_PROXY_HOPS", "client_ip"]
