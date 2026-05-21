"""
@file_name: invite.py
@author: NarraNexus
@date: 2026-05-15
@description: Internal invite-code issuance endpoint (server-to-server).

POST /api/invite/internal/issue is called by the narranexus-website backend
when a visitor submits the invite-code request form. NarraNexus is the
"issuer + store" of codes; the website is the "applicant-facing UI + mailer".

Auth: shared secret in the `X-Internal-Secret` header, matched against the
INTERNAL_INVITE_SECRET env var. The caller is a trusted server, so this
endpoint deliberately:
  - is NOT cloud-mode gated — invite_codes is just a table; issuance works
    against SQLite (local dev) or MySQL (cloud) the same way
  - returns the generated `code` in the response body (the website server
    needs it to compose the email; it never reaches the visitor's browser)
  - does its own input validation but DEFERS rate limiting to the website,
    which sits at the public edge and can do Turnstile / per-IP / per-email
    properly. NarraNexus's only caller is the trusted website server.

Idempotency + cap behaviour:
  - same email with an existing 'used' code  → already_registered (no code)
  - same email with an existing 'issued' code → re-issue (return same code)
  - same email with an existing 'waitlisted'  → waitlisted (no code)
  - else: issue fresh; count_active >= cap → waitlisted (no code)

Registration consumes codes atomically in `routes/auth.py::register()` —
that path is unchanged.
"""

from __future__ import annotations

import os
import re

from fastapi import APIRouter, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from backend.config import settings
from xyz_agent_context.repository import InviteCodeRepository
from xyz_agent_context.utils.db_factory import get_db_client

router = APIRouter()

# Deliberately permissive — full RFC 5322 validation is overkill; we just
# want to reject obvious garbage before a DB round-trip.
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class IssueRequest(BaseModel):
    email: str


class IssueResponse(BaseModel):
    success: bool
    # "issued" | "waitlisted" | "already_registered"
    status: str | None = None
    # Populated ONLY when status == "issued" (a fresh or re-issued live code).
    # Server-to-server return; the website must not propagate it to the browser.
    code: str | None = None
    error: str | None = None


def _require_internal_secret(request: Request) -> None:
    expected = os.environ.get("INTERNAL_INVITE_SECRET", "")
    if not expected:
        # Fail-closed: an operator who hasn't configured the secret hasn't
        # opted into this endpoint. Better a clear 503 than silently
        # accepting anything (or only-accepting-empty-secret).
        raise HTTPException(
            status_code=503,
            detail="invite issuance disabled (INTERNAL_INVITE_SECRET not configured)",
        )
    presented = request.headers.get("X-Internal-Secret", "")
    if presented != expected:
        raise HTTPException(
            status_code=401, detail="invalid or missing X-Internal-Secret"
        )


@router.post("/internal/issue", response_model=IssueResponse)
async def issue_invite(
    payload: IssueRequest, request: Request
) -> IssueResponse:
    _require_internal_secret(request)

    email = (payload.email or "").strip().lower()
    if not _EMAIL_RE.match(email):
        return IssueResponse(success=False, error="invalid email")

    try:
        db = await get_db_client()
        repo = InviteCodeRepository(db)
        existing = await repo.list_for_email(email)

        if any(c.status == "used" for c in existing):
            return IssueResponse(success=True, status="already_registered")

        issued = next((c for c in existing if c.status == "issued"), None)
        if issued:
            # Idempotent: same email, same code. Website re-sends the email.
            return IssueResponse(success=True, status="issued", code=issued.code)

        if any(c.status == "waitlisted" for c in existing):
            return IssueResponse(success=True, status="waitlisted")

        active = await repo.count_active()
        if active >= settings.invite_auto_issue_cap:
            await repo.create(email, status="waitlisted", source="website")
            logger.info(
                "invite: cap {} reached, waitlisted {}",
                settings.invite_auto_issue_cap,
                email,
            )
            return IssueResponse(success=True, status="waitlisted")

        row = await repo.create(email, status="issued", source="website")
        logger.info("invite: issued code to {}", email)
        return IssueResponse(success=True, status="issued", code=row.code)
    except HTTPException:
        raise
    except Exception as e:  # noqa: BLE001
        logger.exception("invite issue failed for {}: {}", email, e)
        return IssueResponse(success=False, error="internal error")
