"""
@file_name: manyfold_diagnostics.py
@author: NexusAgent
@date: 2026-05-25
@description: Container self-diagnostics endpoint for Manyfold operators

Per spec Part 6.7: a single curl shows whether the container is healthy
in every dimension a Manyfold operator might care about — DB reachable,
required env present, file system writable, claude credentials in place,
front-end bundle present.

Registered only when ENABLE_MANYFOLD_API=1. Requires the Manyfold gateway
token.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from loguru import logger

from xyz_agent_context.utils.db_factory import get_db_client


router = APIRouter()


def _require_manyfold_auth(request: Request) -> None:
    if not getattr(request.state, "manyfold_authed", False):
        raise HTTPException(
            status_code=401,
            detail="missing or invalid MANYFOLD_GATEWAY_TOKEN",
        )


def _claude_credentials_present() -> bool:
    """Look for any of the credential paths claude CLI accepts."""
    if os.environ.get("ANTHROPIC_API_KEY"):
        return True
    if os.environ.get("ANTHROPIC_AUTH_TOKEN"):
        return True
    if os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
        return True
    home = Path(os.environ.get("HOME", "/home/app"))
    creds = home / ".claude" / ".credentials.json"
    return creds.is_file()


async def _db_reachable() -> bool:
    try:
        db = await get_db_client()
        # Cheapest no-op: list 0 users with limit semantics. get_one is OK.
        await db.get_one("users", {})
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[manyfold-diag] DB probe failed: {e}")
        return False


def _frontend_dist_present() -> bool:
    """Mirror backend/main.py logic for SPA fallback existence."""
    try:
        from backend.config import settings
        dist = settings.frontend_dist
        return dist.is_dir() and (dist / "index.html").exists()
    except Exception:  # noqa: BLE001
        return False


def _writable(path: str) -> bool:
    p = Path(path)
    if not p.exists():
        return False
    return os.access(p, os.W_OK)


@router.get("/manyfold/diagnostics")
async def diagnostics(request: Request):
    _require_manyfold_auth(request)

    checks: dict[str, Any] = {
        "claude_cli_installed": shutil.which("claude") is not None,
        "claude_credentials_configured": _claude_credentials_present(),
        "frontend_dist_present": _frontend_dist_present(),
        "gateway_token_set": bool(os.environ.get("MANYFOLD_GATEWAY_TOKEN")),
        "writable_data_dir": _writable(os.environ.get("BASE_WORKING_PATH", "/data")) or _writable("/data"),
        "writable_claude_dir": _writable(
            str(Path(os.environ.get("HOME", "/home/app")) / ".claude")
        ),
        "db_reachable": await _db_reachable(),
    }

    image_version = os.environ.get("IMAGE_VERSION", "unknown")
    warnings: list[str] = []
    for k, v in checks.items():
        if not v:
            warnings.append(f"check failed: {k}")

    return {
        "image_version": image_version,
        "manyfold_api_enabled": True,
        "checks": checks,
        "warnings": warnings,
        "all_ok": not warnings,
    }
