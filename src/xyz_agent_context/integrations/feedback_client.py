"""
@file_name: feedback_client.py
@author: Bin Liang
@date: 2026-07-10
@description: Fire-and-forget sender for the NarraNexus feedback intake.

Every deployment form (cloud, self-hosted, local run.sh, DMG) reports to the
same hardcoded team endpoint. What this layer enforces: agent/user identifiers
are hashed before they leave the process, and the summary is truncated. The
summary's CONTENT (no conversation quotes, no keys/PII) is governed by the
callers' prompts/UI copy — it cannot be verified in code.

Opt-out (design decision "B"; design log is author-local, untracked):
`NARRANEXUS_FEEDBACK_DISABLED=1` disables all sends. `NARRANEXUS_FEEDBACK_URL`
overrides the endpoint for dev/test. Both are documented in .env.example /
.env.cloud.example.

send_feedback() must never hurt the caller: one attempt, 3 s timeout, every
exception swallowed (DEBUG log only), returns bool for tests — callers are
free to ignore it.
"""
from __future__ import annotations

import hashlib
import os
from typing import Optional

import httpx
from loguru import logger

DEFAULT_FEEDBACK_URL = "https://agent.narra.nexus/feedback/api/feedback"

CATEGORIES = {"user_dissatisfaction", "repeated_failure", "error", "feature_gap", "other"}
SEVERITIES = {"low", "medium", "high"}


def feedback_url() -> str:
    return os.environ.get("NARRANEXUS_FEEDBACK_URL", "").strip() or DEFAULT_FEEDBACK_URL


def feedback_disabled() -> bool:
    return os.environ.get("NARRANEXUS_FEEDBACK_DISABLED", "").strip() in {"1", "true", "yes"}


def hash_id(value: str) -> str:
    """Short stable hash so the team can count/correlate without identity."""
    if not value:
        return ""
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _app_version() -> str:
    try:
        from xyz_agent_context import __version__
        return __version__
    except Exception:  # noqa: BLE001
        return "unknown"


def _deployment() -> str:
    return os.environ.get("NARRANEXUS_DEPLOYMENT_MODE", "").strip() or "unknown"


async def send_feedback(
    *,
    category: str,
    summary: str,
    severity: str = "medium",
    source: str = "agent",
    agent_id: str = "",
    user_id: str = "",
    channel: str = "",
    client: Optional[httpx.AsyncClient] = None,
) -> bool:
    """Post one feedback report. Never raises; True = accepted by the intake."""
    if feedback_disabled():
        return False
    if category not in CATEGORIES:
        category = "other"
    if severity not in SEVERITIES:
        severity = "medium"
    payload = {
        "category": category,
        "summary": (summary or "").strip()[:500],
        "severity": severity,
        "source": source,
        "deployment": _deployment(),
        "agent_hash": hash_id(agent_id),
        "user_hash": hash_id(user_id),
        "channel": (channel or "")[:32],
        "app_version": _app_version(),
    }
    if not payload["summary"]:
        return False
    try:
        if client is not None:
            resp = await client.post(feedback_url(), json=payload, timeout=3.0)
        else:
            async with httpx.AsyncClient() as own:
                resp = await own.post(feedback_url(), json=payload, timeout=3.0)
        return resp.is_success or resp.status_code == 204
    except Exception as e:  # noqa: BLE001 — reporting must never hurt the caller
        logger.debug(f"[feedback] send failed (ignored): {e}")
        return False
