"""
@file_name: access_log.py
@author: Bin Liang
@date: 2026-04-28
@description: HTTP access log middleware.

Records one structured line per request (method / path / status /
elapsed_ms) and injects two trace fields (run_id, trigger_id) onto
loguru's contextvar so any logger.* call inside a handler is linked
back to that request. Failures inside the handler are surfaced via
logger.exception so the stack trace is captured even when the
handler swallows the original exception upstream.

Three tiers of how loud the access line is:

1. **SKIPPED** — `/health` and similar liveness pings. The middleware
   returns early without emitting anything and without binding trace
   context. There is no scenario where seeing them helps.
2. **DEBUG (high-volume polling)** — frontend GETs that the SystemPage
   sidebar / inbox / status indicators fire on a timer (~6 req/s on
   12 agents). They drown out real signal at INFO. Hidden by default;
   visible by running with NEXUS_LOG_LEVEL=DEBUG. The handler body's
   own logging is unaffected — those handlers should already use
   logger.debug for their per-request diagnostics.
3. **INFO (everything else)** — the default. POSTs, mutating endpoints,
   anything that isn't on the high-volume list.

Failures (5xx via raised exception) always go through logger.exception
regardless of tier — a slow polling endpoint timing out is exactly
when the operator wants to see it.
"""
from __future__ import annotations

import time
from typing import Awaitable, Callable
from uuid import uuid4

from fastapi import Request, Response
from loguru import logger

from xyz_agent_context.utils.logging import bind_event


_SKIP_PATHS: frozenset[str] = frozenset({
    "/health",
})

_SKIP_PREFIXES: tuple[str, ...] = (
    "/api/dashboard/active-sessions",
)

# GETs against these prefixes are typically polled by the frontend
# every few seconds. Keep them at DEBUG so INFO logs stay scannable;
# operators who want to see them can flip NEXUS_LOG_LEVEL=DEBUG and
# get them all back without redeploying.
_HIGH_VOLUME_GET_PREFIXES: tuple[str, ...] = (
    "/api/agent-inbox",
    "/api/providers/embeddings/status",
    "/api/agents/",  # simple-chat-history, awareness, social-network, jobs, etc.
    "/api/auth/agents",  # frontend re-fetches the agent list per page nav
    "/api/jobs",         # JobsPanel polls per-agent job lists on a timer
)


def _should_skip(path: str) -> bool:
    if path in _SKIP_PATHS:
        return True
    return any(path.startswith(p) for p in _SKIP_PREFIXES)


def _is_high_volume_get(method: str, path: str) -> bool:
    if method != "GET":
        return False
    return any(path.startswith(p) for p in _HIGH_VOLUME_GET_PREFIXES)


async def access_log_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Middleware: emit one access line per request, with trace context."""
    path = request.url.path
    method = request.method

    if _should_skip(path):
        return await call_next(request)

    request_id = f"req_{uuid4().hex[:8]}"
    start = time.monotonic()

    bind_kwargs = {
        "run_id": request_id,
        "trigger_id": f"http:{method}:{path}",
    }

    is_polled = _is_high_volume_get(method, path)
    log_emit = logger.debug if is_polled else logger.info

    with bind_event(**bind_kwargs):
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = (time.monotonic() - start) * 1000.0
            # Capture the failure here so the stack survives even if the
            # outer error handler turns this into a generic 500. A
            # failing polled endpoint must not be hidden — always INFO+.
            logger.exception(
                "http.access method={method} path={path} status=500 "
                "elapsed_ms={ms:.1f}",
                method=method,
                path=path,
                ms=elapsed_ms,
            )
            raise
        elapsed_ms = (time.monotonic() - start) * 1000.0
        # Likewise, if a polled endpoint somehow returns 5xx we want it
        # at INFO; only the happy 2xx/3xx path follows the polling tier.
        if is_polled and response.status_code >= 500:
            log_emit = logger.warning
        log_emit(
            "http.access method={method} path={path} status={status} "
            "elapsed_ms={ms:.1f}",
            method=method,
            path=path,
            status=response.status_code,
            ms=elapsed_ms,
        )
        return response
