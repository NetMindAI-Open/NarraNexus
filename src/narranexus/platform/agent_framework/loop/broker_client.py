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

from narranexus.platform.agent_framework.loop.executor_errors import (
    ExecutorUnreachableError,
)


@dataclass(frozen=True)
class ExecutorEnsureResult:
    """Outcome of ensuring a user's executor.

    ``cold_started`` is True when the broker had to spawn a new container
    (vs reuse a warm one) — the signal the run-start flow uses to surface
    the "waking up" UX to the user.

    ``identity_token`` is the broker-signed Ed25519 identity token for this
    user (blueprint P1) — fresh per ensure(), so warm reuse still gets a
    fresh one. None when the broker predates the field or has no signing key
    configured; the dispatch path then simply does not stamp.
    """

    url: str
    cold_started: bool
    identity_token: Optional[str] = None


def broker_url() -> Optional[str]:
    return (os.getenv("BROKER_URL") or "").strip() or None


def executor_seam_active() -> bool:
    """True when THIS process delegates CLI runs to another node.

    Two deployment shapes set the seam: the static single-executor env
    (``AGENT_EXECUTOR_URL``, the RemoteAgentLoopDriver fallback) and the
    broker-managed per-user executors (``BROKER_URL`` — what dev/prod
    compose actually sets; the deploy stack never sets AGENT_EXECUTOR_URL).
    Anything that judges CLI/credential health from LOCAL state (PATH,
    ~/.codex, ~/.claude) must treat "seam active" as "this node cannot
    decide" — checking only the static env var here is how PR #224's
    control-plane guard shipped dead on cloud.
    """
    return bool((os.getenv("AGENT_EXECUTOR_URL") or "").strip() or broker_url())


async def ensure_executor(
    user_id: str,
    *,
    allow_stale_replace: bool = False,
    timeout: float = 120.0,
) -> Optional[ExecutorEnsureResult]:
    """Ensure this user's executor via the broker; return url + cold-start.

    ``allow_stale_replace`` is the CALLER's verdict on whether the broker may
    destroy this user's container to roll a stale executor image — i.e.
    whether anyone is using it right now. This module cannot compute that and
    deliberately does not: it is a transport client, the fact lives in the
    orchestrator's DB, and the decision belongs to whoever holds run context
    (step 3 passes ``executor_reaper.no_live_recorded_run_for``'s answer;
    callers that are not a run leave it False).

    False is the safe default in both directions — it can only ever DELAY an
    image roll, never kill a run — so an omission degrades rather than
    breaks. Named for the one replacement reason it gates: the broker has
    others (an unreachable container, capacity churn) and those must stay
    unconditional, because a container nobody can reach has no run on it to
    protect.

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
            resp = await client.post(
                endpoint,
                json={
                    "user_id": user_id,
                    "allow_stale_replace": allow_stale_replace,
                },
            )
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
    if data.get("stale_replace_deferred"):
        # Loud on purpose: this user runs last deploy's executor code for at
        # least one more turn. Deferring is correct (a run was live on the
        # container) but it must never be silent — a stale executor after a
        # wire-protocol change degrades runs without raising anything
        # (2026-07 mcp_servers rename handed an old executor an EMPTY MCP
        # set). It self-corrects at the next ensure with no live run.
        logger.warning(
            f"[broker] user={user_id} kept a STALE-image executor: a run was "
            f"live on it. It rolls at the next ensure with no live run."
        )
    if not executor_url:
        raise RuntimeError(f"broker returned no executor_url for user {user_id!r}: {data}")
    return ExecutorEnsureResult(
        url=executor_url,
        cold_started=(status == "started"),
        identity_token=data.get("identity_token") or None,
    )


async def executor_healthy(executor_url: str, *, timeout: float = 5.0) -> bool:
    """True iff the executor answers 200 on its /health. Never raises.

    Public: the one health probe for executor containers — callers pass the
    executor's base URL and this appends ``/health`` itself. Hot paths that
    must not stall on a wedged container pass a shorter ``timeout``.
    """
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(f"{executor_url.rstrip('/')}/health")
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
    deadline = time.monotonic() + timeout
    while True:
        if await executor_healthy(executor_url):
            return
        if time.monotonic() >= deadline:
            raise ExecutorUnreachableError(
                f"executor at {executor_url} did not become ready within {timeout}s",
                target=executor_url,
            )
        await asyncio.sleep(interval)


async def stop_executor(user_id: str, *, timeout: float = 30.0) -> bool:
    """Tell the broker to stop this user's executor (idle-cull). True if it
    actually stopped.

    The broker refuses while the CONTAINER reports work in flight, answering
    ``status: "busy"`` with a 200 — it sees holders this side cannot (an
    office-watch session runs inside the container and records no run). A
    refusal is therefore a normal outcome, not an error, and the caller must
    be able to tell it from success: treating it as stopped would drop the
    user's idle stamp and leave the container unreclaimed for good.

    No-op returning True when no broker is configured (nothing to stop, and
    nothing for the caller to hold on to). Transport errors are raised to the
    caller (the reaper), which logs, restamps and moves on.
    """
    base = broker_url()
    if not base:
        return True
    endpoint = f"{base.rstrip('/')}/executors/{user_id}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.delete(endpoint)
        resp.raise_for_status()
        # Defensive about the SHAPE, not the status code: a 2xx whose body is
        # not a JSON object (a proxy's HTML error page, a bare array) would
        # otherwise raise here and be read by the caller as "the stop failed"
        # — reporting the opposite of what happened, for a stop that did.
        try:
            data = resp.json() if resp.content else None
        except ValueError:
            data = None
        status = data.get("status") if isinstance(data, dict) else None
    if status == "busy":
        logger.info(
            f"[broker] executor for user={user_id} is busy; not stopped "
            f"(it holds work this side cannot see)"
        )
        return False
    logger.info(f"[broker] stopped idle executor user={user_id}")
    return True
