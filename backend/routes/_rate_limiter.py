"""
@file_name: _rate_limiter.py
@author: NarraNexus
@date: 2026-04-13
@description: In-memory sliding-window rate limiter for single-process FastAPI.

Used by GET /api/dashboard/agents-status to cap per-viewer request rate at
2 req/s (legitimate dashboard baseline: 0.33 req/s from 3s polling). See
design doc TDR-6 + security critic M-5.

Also the one limiter behind every per-key bucket on the unauthenticated auth
routes (signup / send-code / netmind-login / funnel-report) and the anonymous
channel webhook;
``env_limit`` is the shared parser for operator-tunable limits,
``retry_after`` feeds the 429 ``Retry-After`` header, and ``refund`` returns
a reserved hit when the guarded action did not happen.

Multi-worker deployments: this is process-local; each worker enforces its
own limit. Real-world accepted given current single-process dev + Tauri
sidecar runs. Upgrade to Redis-backed rate limiter alongside Redis session
registry.
"""
from __future__ import annotations

import math
import os
from collections import deque
from time import monotonic


def env_limit(name: str, default: int) -> int:
    """Read a positive integer limit from env var ``name``.

    Unset, empty, non-integer or < 1 values fall back to ``default`` instead
    of raising at import time: a typo in a deploy env must not take the
    backend down, and ``0`` would turn a limiter into "reject everything"
    (``allow`` treats ``limit <= 0`` as a degenerate always-reject config),
    which is never what an operator tuning a rate means.
    """
    try:
        value = int((os.getenv(name) or "").strip())
    except ValueError:
        return default
    return value if value >= 1 else default


class SlidingWindowRateLimiter:
    """Count requests per key in a rolling window; reject beyond `limit`.

    Why deque: O(1) append + popleft; `len()` gives current window count.
    Idle cleanup prevents unbounded dict growth from one-off keys.
    """

    def __init__(
        self,
        limit: int,
        window_sec: float,
        cleanup_interval: int = 100,
    ) -> None:
        self._limit = limit
        self._window = window_sec
        self._deques: dict[str, deque[float]] = {}
        self._request_count = 0
        self._cleanup_interval = cleanup_interval

    def allow(self, key: str) -> bool:
        """Return True if the request is allowed; False if rate-limited.

        A REJECTED call never allocates state for the key — a defensive
        guarantee, and an honest one about its own limits: with
        ``limit > 0`` a NEW key always has a window count of 0 < limit and
        is therefore ALLOWED (and allocated). Key growth happens on the
        allow path. So for a caller-chosen key fed by unauthenticated
        traffic (/api/auth/funnel-report's per-email bucket), the key
        space is capped ONLY by whatever trusted bucket short-circuits in
        front of this one at the call site — the reject-path guarantee
        here is observable solely under the ``limit <= 0`` degenerate
        config and must not be mistaken for that cap.
        """
        self._request_count += 1
        if self._request_count % self._cleanup_interval == 0:
            self._cleanup()
        if self._limit <= 0:
            # Degenerate config always rejected before this restructure too;
            # now it also allocates nothing.
            return False
        now = monotonic()
        dq = self._deques.get(key)
        if dq is not None:
            while dq and dq[0] < now - self._window:
                dq.popleft()
            if len(dq) >= self._limit:
                return False
        else:
            dq = self._deques[key] = deque()
        dq.append(now)
        return True

    def retry_after(self, key: str) -> int:
        """Whole seconds until ``key`` regains a slot (always >= 1).

        Meant for the ``Retry-After`` header right after ``allow(key)``
        returned False. A rejected key's deque holds exactly ``limit``
        in-window hits (``allow`` only appends below the limit), so the slot
        frees when the OLDEST one leaves the window. A key with no state
        (the ``limit <= 0`` degenerate config) reports the full window.
        """
        dq = self._deques.get(key)
        if not dq:
            return max(1, math.ceil(self._window))
        return max(1, math.ceil(dq[0] + self._window - monotonic()))

    def refund(self, key: str) -> None:
        """Hand back one hit previously granted by ``allow(key)``.

        For a caller that reserves a slot BEFORE an outcome is known and keeps
        it only on success (signup charges a created account, not an attempt).
        Reserve-then-refund, rather than check-then-record, keeps the limit
        exact under concurrency: an in-flight attempt holds its slot, so N
        parallel requests cannot all pass a check that none has recorded yet.
        Pops the NEWEST hit: in-window hits are interchangeable for the count,
        and the only drift is that the remaining hits may expire up to one
        in-flight duration sooner. A key with no state is a no-op.
        """
        dq = self._deques.get(key)
        if dq:
            dq.pop()

    def _cleanup(self) -> None:
        now = monotonic()
        to_delete = []
        for k, dq in self._deques.items():
            while dq and dq[0] < now - self._window:
                dq.popleft()
            if not dq:
                to_delete.append(k)
        for k in to_delete:
            del self._deques[k]
