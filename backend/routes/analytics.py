"""Authenticated first-party product analytics ingestion.

Only a small, enumerated frontend event vocabulary is accepted. Identity and
surface are server-derived; payloads contain identifiers and timings only,
never message content or other free-form user data.
"""
from __future__ import annotations

import hashlib
import re

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from backend.auth import resolve_current_user_id
from backend.routes._rate_limiter import SlidingWindowRateLimiter
from narranexus.platform.analytics import track
from narranexus.platform.analytics.events import FRONTEND_EVENTS, PROP_SOURCE

router = APIRouter()
_EVENT_ID_RE = re.compile(r"^[A-Za-z0-9:_-]{8,128}$")
_event_limiter = SlidingWindowRateLimiter(limit=120, window_sec=60.0)


def _frontend_event_id(user_id: str, event: str, client_event_id: str) -> str:
    """Keep browser idempotency keys outside every backend event namespace."""
    digest = hashlib.sha256(
        f"{user_id}:{event}:{client_event_id}".encode("utf-8")
    ).hexdigest()
    return f"fe:{digest}"


class ProductEventRequest(BaseModel):
    event: str = Field(min_length=1, max_length=64)
    event_id: str = Field(min_length=8, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    run_id: str | None = Field(default=None, max_length=128)
    agent_id: str | None = Field(default=None, max_length=128)
    trigger_source: str | None = Field(default=None, max_length=64)
    latency_ms: int | None = Field(default=None, ge=0)


@router.post("/events")
async def capture_product_event(payload: ProductEventRequest, request: Request):
    if payload.event not in FRONTEND_EVENTS:
        raise HTTPException(status_code=400, detail="Unsupported product event")
    if not _EVENT_ID_RE.fullmatch(payload.event_id):
        raise HTTPException(status_code=400, detail="Invalid event_id")

    user_id = await resolve_current_user_id(request)
    if not _event_limiter.allow(user_id):
        raise HTTPException(status_code=429, detail="Too Many Requests")
    properties = {
        PROP_SOURCE: "frontend",
        "session_id": payload.session_id,
        "run_id": payload.run_id,
        "agent_id": payload.agent_id,
        "trigger_source": payload.trigger_source,
        "latency_ms": payload.latency_ms,
    }
    await track(
        user_id=user_id,
        event=payload.event,
        event_id=_frontend_event_id(user_id, payload.event, payload.event_id),
        properties={key: value for key, value in properties.items() if value is not None},
    )
    return {"success": True}
