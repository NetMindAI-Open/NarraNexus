"""
@file_name: feedback.py
@author: Bin Liang
@date: 2026-07-10
@description: Web-UI feedback relay — the frontend dialog posts here and the
backend forwards to the team's feedback intake through feedback_client.

Why relay instead of posting from the browser: no CORS surface on the intake,
the NARRANEXUS_FEEDBACK_DISABLED kill switch applies server-side for the whole
deployment, and in cloud mode the user_id comes from the JWT session (local
mode has no auth by design and falls back to the query param, matching every
other local-mode route).

Privacy: the user's typed text travels verbatim (they wrote it FOR the team);
identifiers are hashed by feedback_client. The send is one awaited attempt
capped at 3 s — the dialog shows a sending state meanwhile.

"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from backend.auth import CurrentUser, get_current_user
from xyz_agent_context.integrations.feedback_client import CATEGORIES, send_feedback

router = APIRouter()


class FeedbackBody(BaseModel):
    category: str = Field(default="other", max_length=32)
    text: str = Field(min_length=1, max_length=500)


@router.post("/feedback")
async def submit_feedback(
    body: FeedbackBody,
    request: Request,
    current_user: Optional[CurrentUser] = Depends(get_current_user),
):
    user_id = current_user.user_id if current_user else (
        request.query_params.get("user_id", "") or "local-user"
    )
    category = body.category if body.category in CATEGORIES else "other"
    delivered = await send_feedback(
        category=category,
        summary=body.text.strip(),
        severity="medium",
        source="web_ui",
        user_id=user_id,
    )
    # delivered=False just means the intake was unreachable or the kill switch
    # is on — the user's ack should not depend on our telemetry plumbing.
    return {"ok": True, "delivered": delivered}
