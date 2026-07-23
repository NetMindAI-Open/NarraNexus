"""
@file_name: broker_client.py
@author:
@date: 2026-06-17
@description: Orchestrator-side client for the per-user Executor Broker.

In cloud, the agent-loop runs in a per-user Executor container that the
broker spawns (only that user's workspace mounted, no platform secrets).
The executor URL is therefore DYNAMIC per user — this resolves it by
asking the broker to ensure the user's executor is up, returning its URL.

Gated on ``BROKER_URL`` (only the cloud orchestrator sets it). When unset
— local/desktop, or the older single static ``AGENT_EXECUTOR_URL`` model
— this returns ``None`` and the caller falls back (in-process driver, or
the static executor URL). So the integration is additive and backward
compatible.

This is the cold-start trigger point: ``ensure`` may spin up a container
(seconds), which is why the timeout is generous and why the run-start
flow surfaces a "waking up" state to the user (see handoff doc).
"""
from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from typing import Optional

import httpx
from loguru import logger

from xyz_agent_context.agent_framework.executor_errors import (
    ExecutorUnreachableError,
)


@dataclass(frozen=True)
class ExecutorEnsureResult:
    """Outcome of ensuring a user's executor.

    ``cold_started`` is True when the broker had to spawn a new container
    (vs reuse a warm one) — the signal the run-start flow uses to surface
    the "waking up" UX to the user.
    """

    url: str
    cold_started: bool


def broker_url() -> Optional[str]:
    return (os.getenv("BROKER_URL") or "").strip() or None


async def ensure_executor(
    user_id: str, *, timeout: float = 120.0
) -> Optional[ExecutorEnsureResult]:
    """Ensure this user's executor via the broker; return url + cold-start.

    Returns ``None`` when no broker is configured (caller falls back).
    Raises on broker/transport error — in cloud we must NOT silently fall
    back to an in-process spawn (that would defeat isolation); the run
    fails loudly instead. A transport failure (broker unreachable) is raised
    as ``ExecutorUnreachableError`` so step_3 surfaces an actionable
    ``infra_transient`` error at cold start instead of a bare httpx exception
    escaping the pipeline (issue ②). HTTP status errors from the broker are
    NOT converted — those flow as-is.
    """
    base = broker_url()
    if not base:
        return None
    endpoint = f"{base.rstrip('/')}/executors"
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(endpoint, json={"user_id": user_id})
            resp.raise_for_status()
            data = resp.json()
    except httpx.TransportError as e:
        raise ExecutorUnreachableError(
            f"broker unreachable at {base}: {type(e).__name__}: {e}",
            target=base,
        ) from e
    executor_url = data.get("executor_url")
    status = data.get("status")
    logger.info(
        f"[broker] ensured executor user={user_id} status={status} url={executor_url}"
    )
    if not executor_url:
        raise RuntimeError(f"broker returned no executor_url for user {user_id!r}: {data}")
    return ExecutorEnsureResult(url=executor_url, cold_started=(status == "started"))


async def _executor_healthy(health_url: str) -> bool:
    """True iff the executor answers 200 on its /health. Never raises."""
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(health_url)
            return resp.status_code == 200
    except Exception:  # noqa: BLE001 — still booting / not reachable yet
        return False


async def wait_until_ready(
    executor_url: str, *, timeout: float = 60.0, interval: float = 0.5
) -> None:
    """Block until a freshly cold-started executor finishes booting.

    A new container takes a few seconds to bring uvicorn up on :8020;
    connecting to ``/agent-loop`` before then races the startup and fails
    into the fallback path. This polls the executor's ``/health`` until it
    answers — a condition-based wait, NOT a fixed sleep, and NOT a cap on the
    agent loop (binding rule #14): it only waits for infrastructure to become
    ready. Raises ``ExecutorUnreachableError`` if the executor never comes up
    within ``timeout`` (a genuinely broken container — failing loudly is
    correct, and the typed exception lets step_3 surface an actionable
    ``infra_transient`` error rather than a bare RuntimeError).
    """
    health = f"{executor_url.rstrip('/')}/health"
    deadline = time.monotonic() + timeout
    while True:
        if await _executor_healthy(health):
            return
        if time.monotonic() >= deadline:
            raise ExecutorUnreachableError(
                f"executor at {executor_url} did not become ready within {timeout}s",
                target=executor_url,
            )
        await asyncio.sleep(interval)


async def stop_executor(user_id: str, *, timeout: float = 30.0) -> None:
    """Tell the broker to stop this user's executor (idle-cull).

    No-op when no broker is configured. Best-effort: a transport error is
    raised to the caller (the reaper), which logs and moves on — the
    broker's own label-based reaper is the backstop for orphans.
    """
    base = broker_url()
    if not base:
        return
    endpoint = f"{base.rstrip('/')}/executors/{user_id}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.delete(endpoint)
        resp.raise_for_status()
    logger.info(f"[broker] stopped idle executor user={user_id}")
