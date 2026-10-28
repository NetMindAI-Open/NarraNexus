"""
@file_name: gateway_key_misuse.py
@author: Bin Liang
@date: 2026-08-19
@description: Internal admin endpoint that records an authoritative gateway-key
misuse event — the SOLE writer of gateway_key_misuse.

    POST /api/admin/gateway-key-misuse    record one gateway-key misuse event

Neutral / opaque by design: it stores a ``user_id`` that the caller already
AUTHORITATIVELY reverse-resolved for the offending key (the gateway is the
authority on which identity the key is bound to). This endpoint parses NO
free-form text for attribution — it records only the fields it is handed. Gated
on ``X-Admin-Secret`` (the same lock as suspend / migrate-identity), so only the
internal server-to-server path can write here; executor / agent hold no such
credential.

The written row is the payload, not an advisory note: the security monitor reads
gateway_key_misuse read-only to drive its response ladder. So the write goes
through the repository layer ([[gateway_key_misuse_repository]]) and a failure
surfaces (the row was not persisted), rather than being swallowed.
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends
from loguru import logger
from pydantic import BaseModel

# Re-exported so the admin secret can be overridden per-test via ``mod.settings``
# (this module's namespace); the shared ``require_admin_secret`` helper reads the
# same ``settings`` singleton object.
from narranexus.platform.settings import settings  # noqa: F401
from narranexus.platform.utils.db.db_factory import get_db_client
from narranexus.platform.utils.db.schema_registry import varchar_width
from narranexus.platform.utils.timezone import to_datetime6_literal
from narranexus.platform.repository.gateway_key_misuse_repository import (
    GatewayKeyMisuseRepository,
)

from ._admin_secret import admin_secret_dependency

router = APIRouter(prefix="/api/admin", tags=["admin"])

# Field widths are the schema registry's single source of truth (avoid a second
# copy that could drift from the DDL). The route clips every attacker-influenced
# field to its column width so an over-long value never drops the event; the
# over-width ``user_id`` threshold uses the SAME width, so a value too long to be
# a real id can never be mistaken for an authoritative attribution.
_MISUSE_TABLE = GatewayKeyMisuseRepository.TABLE
USER_ID_MAX_LEN = varchar_width(_MISUSE_TABLE, "user_id")
RUN_ID_MAX_LEN = varchar_width(_MISUSE_TABLE, "run_id")
KEY_HASH_MAX_LEN = varchar_width(_MISUSE_TABLE, "key_hash")
CALLER_IP_MAX_LEN = varchar_width(_MISUSE_TABLE, "caller_ip")
CALLER_UA_MAX_LEN = varchar_width(_MISUSE_TABLE, "caller_ua")
MODEL_MAX_LEN = varchar_width(_MISUSE_TABLE, "model")


class GatewayKeyMisuseRequest(BaseModel):
    # Authoritative reverse-resolved id, or None for an alert-only row. Every
    # field is what the caller resolved/observed; this endpoint adds no
    # interpretation. Unknown fields are ignored (pydantic drops them) — an
    # attacker cannot smuggle an alternative attribution in.
    #
    # NO length validators on any field: the values here are attacker-influenced
    # (a caller forwards what it observed, e.g. the presented caller_ua). A
    # max_length would turn an over-long field into a 422 — i.e. a DROPPED event,
    # which is a missed enforcement. Every event MUST land, so over-long fields
    # are CLIPPED server-side (see ``_clip``) instead of rejected.
    user_id: Optional[str] = None
    run_id: Optional[str] = None
    key_hash: Optional[str] = None
    caller_ip: Optional[str] = None
    caller_ua: Optional[str] = None
    model: Optional[str] = None
    # Authoritative time the misuse occurred (caller-supplied). When present and
    # parseable it is the idempotency anchor: a retry of the same event carries
    # the same (key_hash, hit_at) and collapses to one row (the UNIQUE index).
    # Accepted as an ISO-8601 instant WITH a time-of-day (a trailing ``Z`` is
    # honoured); it is normalised server-side to the DATETIME(6) contract
    # ``YYYY-MM-DD HH:MM:SS.ffffff`` (UTC) before it is written. Omitted,
    # unparseable, or too coarse (a bare date / hour-only value that would
    # collapse distinct events) → the column default (insert time) applies and no
    # dedup is possible.
    hit_at: Optional[str] = None


class GatewayKeyMisuseResponse(BaseModel):
    id: int
    # False on a fresh insert; True when an at-least-once retry (same
    # (key_hash, hit_at)) collapsed onto an existing row. Lets the caller / the
    # deploy side observe the retry rate instead of a field that is always True.
    already: bool


def _has_time_of_day(raw: str) -> bool:
    """True if an ISO-8601 string carries a real time-of-day (>= ``HH:MM``).

    ``datetime.fromisoformat`` happily accepts a bare date (``"2026-08-19"``) or
    an hour-only value; both normalise to a VALID but COARSE ``DATETIME(6)``
    literal (midnight / the top of the hour). A coarse hit_at defeats the
    idempotency anchor — it would collapse distinct misuse events that merely
    share a day/hour onto ONE ``(key_hash, hit_at)`` row. A genuine instant has a
    date/time separator and a minute field, i.e. a ``':'`` — so require one.
    """
    return ":" in raw


def _clip(value: Optional[str], limit: int) -> Optional[str]:
    """Truncate an attacker-influenced string field to its column width.

    None passes through untouched. A value longer than the column would be
    rejected by MySQL on write (a str too long for its VARCHAR is a write
    error, i.e. a lost row); every misuse event MUST be recorded — a dropped
    row is a missed enforcement — so we clip rather than reject. The leading
    bytes still carry the signal a human or the monitor needs.
    """
    if value is None:
        return None
    return value[:limit]


@router.post("/gateway-key-misuse", response_model=GatewayKeyMisuseResponse)
async def record_gateway_key_misuse(
    request: GatewayKeyMisuseRequest,
    _: None = Depends(admin_secret_dependency),
) -> GatewayKeyMisuseResponse:
    # Clip every attacker-influenced field to its column width. An event must
    # ALWAYS land: never 422/500 a real misuse event away just because a field
    # is over-long (dropping one row = missing one enforcement).
    run_id = _clip(request.run_id, RUN_ID_MAX_LEN)
    key_hash = _clip(request.key_hash, KEY_HASH_MAX_LEN)
    caller_ip = _clip(request.caller_ip, CALLER_IP_MAX_LEN)
    caller_ua = _clip(request.caller_ua, CALLER_UA_MAX_LEN)
    model = _clip(request.model, MODEL_MAX_LEN)

    # user_id is special: it drives enforcement, so we NEVER truncate it — a
    # clipped id could collide with a DIFFERENT real user (a mis-attributed
    # ban). An over-long id is treated as "reverse-resolution failed" and
    # recorded as an alert-only row (user_id=NULL, disposition stays 'pending')
    # for a human to triage; the event still lands, we just refuse to guess who.
    user_id = request.user_id
    if user_id is not None and len(user_id) > USER_ID_MAX_LEN:
        logger.error(
            f"[gateway-key-misuse] user_id over {USER_ID_MAX_LEN} chars "
            f"(len={len(user_id)}) — recording alert-only row with NULL id"
        )
        user_id = None

    # Normalise hit_at to the DATETIME(6) contract before it is written. A
    # DATETIME(6) column rejects an illegal literal on MySQL (error 1292), which
    # would 500 the endpoint and DROP the event — but every event MUST land. So
    # an unparseable hit_at is DROPPED (the column default = insert time applies)
    # and the event still records; only its idempotency anchor is lost. The
    # normalised value is what the repository both writes AND reverse-looks-up on
    # a dedup collision, so the two paths compare identical bytes.
    hit_at = request.hit_at
    if hit_at is not None:
        normalized = to_datetime6_literal(hit_at)
        # A parseable-but-coarse value (bare date / hour-only) is treated as
        # unusable: it would collapse distinct events onto one idempotency anchor.
        # Drop it just like an unparseable value — the event still lands on the
        # column-default timestamp, only its (key_hash, hit_at) dedup is lost.
        if normalized is not None and not _has_time_of_day(hit_at):
            normalized = None
        if normalized is None:
            logger.error(
                f"[gateway-key-misuse] unusable hit_at {hit_at!r} — dropping "
                "it; event lands with the column-default timestamp"
            )
        hit_at = normalized

    db = await get_db_client()
    row_id, already = await GatewayKeyMisuseRepository(db).record(
        user_id=user_id,
        run_id=run_id,
        key_hash=key_hash,
        caller_ip=caller_ip,
        caller_ua=caller_ua,
        model=model,
        hit_at=hit_at,
    )
    return GatewayKeyMisuseResponse(id=row_id, already=already)
