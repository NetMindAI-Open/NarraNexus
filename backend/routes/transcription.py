"""
@file_name: transcription.py
@author: Bin Liang
@date: 2026-05-07
@description: Transcription availability check (JWT-protected)

Single route used by the chat UI on mount to decide whether the mic
button should record-on-click or open the "configure a provider"
dialog. We intentionally pre-check rather than letting the user
record first and surface the failure post-upload — recording mid-call
only to be told it can't be transcribed is a worse UX.

The route consults :class:`TranscriptionService.availability_reason`
which walks the same resolver chain as the actual transcription path,
so "available here, fails on real upload" is impossible barring race
(provider deleted between mount and click — handled by the existing
upload-time `transcription_available` echo).
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from pydantic import BaseModel

from backend.auth import resolve_current_user_id
from xyz_agent_context.agent_framework.transcription import TranscriptionService


router = APIRouter()


class TranscriptionAvailabilityResponse(BaseModel):
    available: bool
    # One of the constants from
    # ``transcription.service.TranscriptionAvailability`` —
    # "has_openai" | "has_netmind" | "has_other" | "system_free_tier" | "none"
    # Frontend reads this for analytics and to vary the dialog wording
    # (e.g. tell free-tier users they don't need to configure anything).
    reason: str


@router.get("/availability", response_model=TranscriptionAvailabilityResponse)
async def availability(request: Request):
    """Tell the frontend whether voice input is usable for this user.

    Identity from auth_middleware (X-User-Id local, JWT cloud). The old
    `?user_id=` fallback was a relic from the single-user-local era and
    has been removed — local now requires X-User-Id like cloud requires
    a JWT (auth_middleware enforces this before the handler runs).
    """
    uid = await resolve_current_user_id(request)
    available, reason = await TranscriptionService.instance().availability_reason(uid)
    return TranscriptionAvailabilityResponse(available=available, reason=reason)
