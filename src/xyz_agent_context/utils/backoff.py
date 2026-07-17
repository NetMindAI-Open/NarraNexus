"""
@file_name: backoff.py
@author:
@date: 2026-07-13
@description: Shared exponential-backoff math for failure cooldowns.

Extracted so the real-time-layer Agent circuit-breaker
(``agent_framework/agent_circuit_breaker.py``) and any future caller share
ONE definition of "how long should a failing thing wait before its next
retry". The Job scheduler (``module/job_module/job_trigger.py``) keeps its
own inline copy for now (deliberately not migrated — see the circuit-breaker
plan, scope decision #2); this module is the go-forward home for the formula.

This is pure arithmetic — no I/O, no state. It only spaces RETRIES of things
that already finished and failed; it never caps a running loop (binding
rule #14).
"""

from __future__ import annotations

# Backoff schedule: base · 2^(n-1), clamped to the cap.
# n=1 → 60s, 2 → 120s, 3 → 240s, … capped at 3600s (1h).
DEFAULT_BACKOFF_BASE_SECONDS = 60
DEFAULT_BACKOFF_CAP_SECONDS = 3600


def compute_cooldown_seconds(
    consecutive_failures: int,
    *,
    base: int = DEFAULT_BACKOFF_BASE_SECONDS,
    cap: int = DEFAULT_BACKOFF_CAP_SECONDS,
) -> int:
    """Exponential backoff for the Nth consecutive failure.

    Args:
        consecutive_failures: the running failure count (1-based). Values < 1
            are treated as 1 so the first failure always yields ``base``.
        base: first-failure cooldown in seconds.
        cap: maximum cooldown in seconds (the schedule plateaus here).

    Returns:
        Cooldown in seconds: ``min(base * 2**(n-1), cap)``.
    """
    n = max(1, consecutive_failures)
    return min(base * (2 ** (n - 1)), cap)
