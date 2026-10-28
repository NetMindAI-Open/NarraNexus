"""
@file_name: diagnostics.py
@author: NexusAgent
@date: 2026-05-25
@description: Diagnostics surface for Manyfold operators — container
health plus the per-agent pull channel.

Two generations live here:

1. ``GET /manyfold/diagnostics`` (2026-05-25, spec Part 6.7): one curl
   shows whether the container is healthy — DB reachable, env present,
   file system writable, credentials in place.
2. The per-agent PULL channel (2026-08-10, observability plan A): the
   on-demand half of the observability design. The push half (log sink)
   ships what the process chose to emit; these endpoints answer what
   push can't — DB content that never ships (events full text), sink
   gaps, ad-hoc queries — and they work when the sink itself is broken
   (the two paths deliberately share no machinery):

   - ``…/agents/{id}/diagnostics/ingress``   — audit-trail query (the
     managed-ingress lifecycle rows + manyfold files-write rows)
   - ``…/agents/{id}/diagnostics/events``    — run summaries; full
     content only per-id (heavy + sensitive ⇒ explicit pull)
   - ``…/diagnostics/logs/services|tail``    — service-log tail. NOT
     agent-scoped: logs are process-level. The access boundary is the
     RUNTIME, not the user: the gateway token is a RUNTIME-level
     credential — and not an operator secret: manyfoldFragmentAuth.ts
     hands it to the browser of every end user arriving from Manyfold.
     Any such holder can already read every agent's workspace in this
     runtime through the files API (including the multi-Manyfold-user
     runtimes manyfold/agents.py + backend/auth.py support), so process
     logs expose no audience broader than that existing surface.

Registered only when ENABLE_MANYFOLD_API=1. Requires the Manyfold gateway
token. All new endpoints are read-only; known credential shapes are
redacted from every response (cheap insurance, not a formal guarantee).
"""

from __future__ import annotations

import json
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from loguru import logger

from narranexus.platform.repository.channel_trigger_audit_repository import (
    ChannelTriggerAuditRepository,
)
from narranexus.platform.repository.event_repository import EventRepository
from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils.db.dialect_time import event_time_str
from backend.routes.admin.logs import (
    _filter_by_level,
    _list_files,
    _log_root,
    _resolve_log_path,
    _resolve_service_dir,
    _tail_lines,
)
from backend.auth_errors import GATEWAY_TOKEN_INVALID, AuthError


router = APIRouter()


def _require_manyfold_auth(request: Request) -> None:
    if not getattr(request.state, "manyfold_authed", False):
        raise AuthError(
            GATEWAY_TOKEN_INVALID,
            "missing or invalid MANYFOLD_GATEWAY_TOKEN",
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


# ---------------------------------------------------------------------------
# Per-agent pull channel (observability plan A, 2026-08-10)
# ---------------------------------------------------------------------------

# Response caps. `limit` bounds row counts (pushed into SQL — a
# long-running agent's events row set is exactly what must never be
# materialized whole); the per-field cap (CHARACTERS, not bytes — CJK
# text can be ~3x in UTF-8) stops one MEDIUMTEXT column from blowing
# the response past what a diagnostic client wants to page through.
_MAX_ROWS = 200
_DEFAULT_ROWS = 50
_MAX_FIELD_CHARS = 512 * 1024
_MAX_TAIL_LINES = 2000

# Known credential shapes, redacted from every response body. Two forms:
# JSON-ish `"key": "value"` pairs (audit details / env_context) and
# bearer headers that leaked into log lines. Insurance against the known
# offenders, not a formal no-secrets guarantee.
_CREDENTIAL_KEY_RE = re.compile(
    r'("(?:context_token|reply_token|bot_token|app_secret|app_token|'
    r'access_token|matrix_access_token|api_key|gateway_token)"\s*:\s*")'
    r'[^"]*(")'
)
_BEARER_RE = re.compile(r"(Bearer\s+)[A-Za-z0-9._\-]{8,}")


def _redact(text: str) -> str:
    text = _CREDENTIAL_KEY_RE.sub(r"\1[redacted]\2", text)
    return _BEARER_RE.sub(r"\1[redacted]", text)


def _clip(value: Any) -> str:
    text = "" if value is None else str(value)
    if len(text) > _MAX_FIELD_CHARS:
        return (
            text[:_MAX_FIELD_CHARS]
            + f"\n…[truncated {len(text) - _MAX_FIELD_CHARS} chars]"
        )
    return text


def _redact_clip(value: Any) -> str:
    """Redact FIRST, clip second: clipping first can cut a credential's
    closing quote at the boundary, defeating the key-pattern regex and
    leaking the token prefix."""
    return _clip(_redact("" if value is None else str(value)))


def _clamp_limit(limit: int) -> int:
    return max(1, min(limit, _MAX_ROWS))


def _norm_time(value: Any) -> str:
    """Comparable space-form string for a DATETIME cell (one shared home
    for the sqlite-datetime-vs-mysql-string asymmetry: utils/db)."""
    return event_time_str(value).replace("T", " ")


def _since_floor(since: str) -> str:
    """Parse a caller's `since` into the comparable floor string.

    Real parsing, not separator folding: full ISO permits timezone
    designators, and a lexicographic compare of `…09:00:00Z` against a
    stored `…09:00:00.5+00:00` sorts the WRONG way at the boundary
    second ('Z' > '.'). Aware inputs convert to UTC and drop the
    offset; the stored strings' own `+00:00` suffix sorts above any
    fraction-less floor prefix, so prefix comparison stays correct.
    Unparseable input is a 400, not a silent empty result."""
    raw = since.strip().replace(" ", "T").replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(raw)
    except ValueError:
        # from None: the ValueError adds nothing over the message and
        # would double the traceback in request logs.
        raise HTTPException(
            status_code=400,
            detail=f"since must be ISO datetime, got {since!r}",
        ) from None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed.isoformat(sep=" ")


@router.get("/manyfold/agents/{agent_id}/diagnostics/ingress")
async def agent_ingress_audit(
    agent_id: str,
    request: Request,
    since: Optional[str] = Query(None, description="ISO lower bound on event_time"),
    event_type: Optional[str] = Query(None),
    limit: int = Query(_DEFAULT_ROWS, description=f"max {_MAX_ROWS}"),
):
    """Audit-trail slice for one agent, newest first.

    The first consumer of the managed-ingress audit events (#267):
    denied / silent / attachments / processed rows plus this agent's
    manyfold files-write rows — "what happened to my message" as one
    query instead of a probe session.
    """
    _require_manyfold_auth(request)
    db = await get_db_client()
    # Query shape (projection/order/limit) is the repository's business;
    # `since` filters the already-bounded page here (newest-first means
    # the page IS the newest slice of the window).
    rows = await ChannelTriggerAuditRepository.recent_for_agent(
        db, agent_id, event_type=event_type, limit=_clamp_limit(limit)
    )
    if since:
        floor = _since_floor(since)
        rows = [r for r in rows if _norm_time(r.get("event_time")) >= floor]
    out = []
    for r in rows:
        details_raw = _redact(str(r.get("details") or "{}"))
        try:
            details = json.loads(details_raw)
        except ValueError:
            details = {"_raw": details_raw[:1000]}
        out.append(
            {
                "event_time": _norm_time(r.get("event_time")),
                "event_type": r.get("event_type"),
                "channel": r.get("channel"),
                "message_id": r.get("message_id"),
                "chat_id": r.get("chat_id"),
                "sender_id": r.get("sender_id"),
                "details": details,
            }
        )
    return {"data": out, "object": "list"}


@router.get("/manyfold/agents/{agent_id}/diagnostics/events")
async def agent_events_summary(
    agent_id: str,
    request: Request,
    since: Optional[str] = Query(None, description="ISO lower bound on started_at"),
    limit: int = Query(_DEFAULT_ROWS, description=f"max {_MAX_ROWS}"),
):
    """Run summaries, newest first — deliberately WITHOUT content fields.

    The list answers "which runs happened and how did they end"; the
    heavy, user-content-bearing columns (env_context / final_output /
    event_log) never leave the DB here — SQL projection excludes them —
    and are only served per-id by the endpoint below (DB is pull-only
    by policy).
    """
    _require_manyfold_auth(request)
    db = await get_db_client()
    rows = await EventRepository(db).diagnostic_summaries(
        agent_id, limit=_clamp_limit(limit)
    )
    if since:
        floor = _since_floor(since)
        rows = [r for r in rows if _norm_time(r.get("started_at")) >= floor]
    out = [
        {
            "event_id": r.get("event_id"),
            "trigger": r.get("trigger"),
            "trigger_source": r.get("trigger_source"),
            "state": r.get("state"),
            "started_at": _norm_time(r.get("started_at")),
            "finished_at": _norm_time(r.get("finished_at")),
            "tool_call_count": r.get("tool_call_count"),
            "current_stage": r.get("current_stage"),
            # error text is the field most likely to embed upstream
            # credentials (4xx bodies quote Authorization headers whole).
            "error_message": _redact_clip(r.get("error_message"))[:500] or None,
            "narrative_id": r.get("narrative_id"),
        }
        for r in rows
    ]
    return {"data": out, "object": "list"}


@router.get("/manyfold/agents/{agent_id}/diagnostics/events/{event_id}")
async def agent_event_full(agent_id: str, event_id: str, request: Request):
    """One run's full record — input, output, tool log.

    Scoped WHERE agent_id AND event_id: an event belonging to another
    agent 404s identically to a missing one (no existence oracle).
    Credential shapes are redacted; oversized fields are clipped with an
    explicit truncation marker.
    """
    _require_manyfold_auth(request)
    db = await get_db_client()
    row = await EventRepository(db).diagnostic_full(agent_id, event_id)
    if not row:
        raise HTTPException(status_code=404, detail="no such event for this agent")
    return {
        "event_id": row.get("event_id"),
        "trigger": row.get("trigger"),
        "trigger_source": row.get("trigger_source"),
        "state": row.get("state"),
        "started_at": _norm_time(row.get("started_at")),
        "finished_at": _norm_time(row.get("finished_at")),
        "tool_call_count": row.get("tool_call_count"),
        "current_stage": row.get("current_stage"),
        "error_message": _redact_clip(row.get("error_message")) or None,
        "narrative_id": row.get("narrative_id"),
        "env_context": _redact_clip(row.get("env_context")),
        "final_output": _redact_clip(row.get("final_output")),
        "event_log": _redact_clip(row.get("event_log")),
        "module_instances": _redact_clip(row.get("module_instances")),
    }


@router.get("/manyfold/diagnostics/logs/services")
async def logs_services(request: Request):
    """Which service logs exist in this sandbox, with their dates."""
    _require_manyfold_auth(request)
    root = _log_root()
    services: dict[str, Any] = {}
    if root.is_dir():
        for d in sorted(root.iterdir()):
            if d.is_dir():
                services[d.name] = [f["name"] for f in _list_files(d)]
    return {"log_root": str(root), "services": services}


@router.get("/manyfold/diagnostics/logs/tail")
async def logs_tail(
    request: Request,
    service: str = Query(...),
    lines: int = Query(200, description=f"max {_MAX_TAIL_LINES}"),
    date: Optional[str] = Query(None, description="YYYYMMDD; default today"),
    level: Optional[str] = Query(None),
    grep: Optional[str] = Query(None, max_length=200, description="plain substring"),
):
    """Tail one service's log without shelling into the sandbox.

    Reuses the admin logs read helpers (file resolution / seek-tail /
    level filter) — read logic shared, auth deliberately NOT: this stays
    on the gateway token, /api/admin keeps its session auth. ``grep`` is
    a plain substring match, not a regex (no ReDoS surface).
    """
    _require_manyfold_auth(request)
    lines = max(1, min(lines, _MAX_TAIL_LINES))
    service_dir = _resolve_service_dir(service)
    path = _resolve_log_path(service_dir, service, date)
    tail = _filter_by_level(_tail_lines(path, lines), level)
    if grep:
        tail = [ln for ln in tail if grep in ln]
    return {
        "service": service,
        "file": path.name,
        "lines": [_redact(ln) for ln in tail[-lines:]],
    }
