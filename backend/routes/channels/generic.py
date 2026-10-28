"""
@file_name: generic.py
@author: Bin Liang
@date: 2026-09-04
@description: Generic channel routes (``/api/channels/{channel}/…``): schema, bind, credential, test, unbind, set-active for ANY channel in ``ingress.channels``.

The ONE binding surface since batch 4d.3: the six builtin channels' bespoke
``/api/<channel>/{bind,credential,test,unbind,set-active}`` routes are retired
(their channel-specific flows — Lark OAuth, WeChat QR, NarraMessenger prewarm —
stay on their own routers). Manager-backed channels delegate bind/test/unbind
to the data-access seam (their services keep validating with the external
platform; the bind body is checked against the descriptor's ``bind_fields``),
plugin channels are validated against their credential schema and stored
directly; credential reads and the active flag are the generic store for every
channel. Ownership is the one canonical check (``_ownership.check_owned``).
"""
from __future__ import annotations

import json
from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Path, Request
from loguru import logger
from pydantic import BaseModel, Field

from backend.routes._client_ip import client_ip
from backend.routes._rate_limiter import SlidingWindowRateLimiter
from backend.routes._ownership import check_owned
from narranexus.contracts.channel import ChannelDescriptor
from narranexus.platform.channel.credential_store import CredentialConflict, GenericCredentialStore, UnknownChannel, bind_fields_for, descriptor_for, validate_bind_fields
from narranexus.platform.channel.webhook_inbox import WebhookInbox
from narranexus.platform.channel.webhook_transport import SECRET_FIELD, new_webhook_secret, verify_webhook
from narranexus.platform.utils.route_envelope import structured_envelope

router = APIRouter()

# Anonymous inbound webhooks: per binding and per source address. The source
# address comes from the shared proxy-hop helper, never `request.client.host`:
# uvicorn runs without --proxy-headers behind the deploy stack's nginx, so the
# socket peer is the SAME container address for every cloud request and this
# limiter would be one global 600/min bucket that any single abuser could
# exhaust, 429ing every agent's inbound webhooks at once.
_webhook_limiter = SlidingWindowRateLimiter(limit=120, window_sec=60.0)
_webhook_ip_limiter = SlidingWindowRateLimiter(limit=600, window_sec=60.0)

# Safe agent_id values (alphanumeric + underscore + hyphen) — the same shape the
# retired per-channel routes enforced, so an id never carries path/query syntax.
_SAFE_ID_PATTERN = r"^[a-zA-Z0-9_\-]+$"


class AgentBody(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)


class BindBody(AgentBody):
    fields: dict[str, Any] = Field(default_factory=dict)


class SetActiveBody(AgentBody):
    active: bool


async def _db():
    from narranexus.platform.utils.db.db_factory import get_db_client

    return await get_db_client()


def _descriptor(channel: str) -> ChannelDescriptor:
    try:
        return descriptor_for(channel)
    except UnknownChannel:
        raise HTTPException(status_code=404, detail=f"unknown channel: {channel}") from None


def _field_view(f: Any) -> dict[str, Any]:
    return {"name": f.name, "kind": f.kind, "label": f.label or f.name, "help": f.help, "required": f.required, "options": list(f.options), "public": f.public}


def _schema_view(d: ChannelDescriptor) -> dict[str, Any]:
    return {
        "channel": d.name,
        "display_name": d.display_name,
        "transport": d.transport,
        "has_bind": d.has_bind,
        "has_test": d.has_test and d.credential_schema.supports_test,
        "manager_backed": bool(d.credential_manager_ref),
        "fields": [_field_view(f) for f in d.credential_schema.fields],
        # What a bind call takes (differs from the stored fields for manager-backed channels).
        "bind_fields": [_field_view(f) for f in bind_fields_for(d)],
        "external_id_field": d.credential_schema.external_id_field,
    }


@router.get("/{channel}/schema")
@structured_envelope("channels")
async def channel_schema(channel: str) -> dict[str, Any]:
    return {"success": True, "data": _schema_view(_descriptor(channel))}


@router.post("/{channel}/bind")
@structured_envelope("channels")
async def channel_bind(request: Request, channel: str, body: BindBody) -> dict[str, Any]:
    d = _descriptor(channel)
    auth_err = await check_owned(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    if not d.has_bind:
        return {"success": False, "error": f"{d.display_name} binds through its own flow"}
    invalid = validate_bind_fields(d, body.fields)
    if invalid:
        return {"success": False, "error": invalid}
    db = await _db()
    if d.credential_manager_ref:
        # The channel's own service validates with the external platform and persists
        # through its manager (generic store); its envelope is returned verbatim.
        from narranexus.platform.module_system.data_access.channel_store import DirectStore

        result = await DirectStore().bind(channel, body.agent_id, body.fields)
        if result.get("success"):
            logger.info(f"[channels] {channel} bound: agent={body.agent_id}")
        return result
    fields = dict(body.fields)
    issued_secret: Optional[str] = None
    if d.transport == "webhook" and not str(fields.get(SECRET_FIELD, "") or "").strip():
        # The binding's inbound token, generated once and shown once (it is a secret).
        existing = await GenericCredentialStore(db).get(channel, body.agent_id)
        kept = existing.secret.get(SECRET_FIELD) if existing else None
        fields[SECRET_FIELD] = kept or new_webhook_secret()
        issued_secret = None if kept else fields[SECRET_FIELD]  # shown once, at first bind
    try:
        record = await GenericCredentialStore(db).upsert(channel, body.agent_id, fields, enabled=True)
    except CredentialConflict as conflict:
        raise HTTPException(status_code=409, detail=str(conflict)) from None
    logger.info(f"[channels] {channel} bound: agent={body.agent_id}")
    data = record.to_public_dict()
    if d.transport == "webhook":
        data["webhook_path"] = f"/api/channels/{channel}/webhook/{body.agent_id}"
        if issued_secret:
            data["webhook_secret"] = issued_secret
    return {"success": True, "data": data}


@router.post("/{channel}/webhook/{agent_id}")
async def channel_webhook(request: Request, channel: str, agent_id: str = Path(..., pattern=_SAFE_ID_PATTERN)) -> dict[str, Any]:
    """Inbound webhook for a webhook-transport channel: verify the binding's secret, append the event to the inbox.

    Auth-exempt at the middleware (no session) — the credential's
    ``webhook_secret`` IS the auth: ``X-Webhook-Token`` or an HMAC-SHA256
    signature of the raw body in ``X-Webhook-Signature``. Never a query
    parameter: reverse proxies log the request line, and a secret in it is a
    secret in every access log. Unknown binding and bad secret answer the same
    401 (the difference is logged server-side) so the endpoint is not an agent
    enumeration oracle, and a sliding window bounds anonymous DB reads.
    """
    d = _descriptor(channel)
    if d.transport != "webhook":
        raise HTTPException(status_code=404, detail=f"{channel} is not a webhook channel")
    if not _webhook_limiter.allow(f"{channel}:{agent_id}") or not _webhook_ip_limiter.allow(client_ip(request)):
        raise HTTPException(status_code=429, detail="too many webhook requests")
    raw = await request.body()
    record = await GenericCredentialStore(await _db()).get(channel, agent_id)
    if record is None or not record.enabled or not record.readable:
        logger.info(f"[channels] {channel} webhook for {agent_id}: no active binding (answered 401)")
        raise HTTPException(status_code=401, detail="webhook token or signature invalid")
    if not verify_webhook(str(record.secret.get(SECRET_FIELD, "") or ""), raw, dict(request.headers)):
        raise HTTPException(status_code=401, detail="webhook token or signature invalid")
    try:
        payload = json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise HTTPException(status_code=400, detail="body must be JSON") from None
    if not isinstance(payload, dict):
        payload = {"value": payload}
    await WebhookInbox(await _db()).push(channel, agent_id, payload)
    return {"success": True, "queued": True}


@router.get("/{channel}/credential")
@structured_envelope("channels")
async def channel_credential(request: Request, channel: str, agent_id: str) -> dict[str, Any]:
    _descriptor(channel)
    auth_err = await check_owned(request, agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    public = await GenericCredentialStore(await _db()).get_public(channel, agent_id)
    return {"success": True, "data": public}


@router.post("/{channel}/test")
@structured_envelope("channels")
async def channel_test(request: Request, channel: str, body: AgentBody) -> dict[str, Any]:
    d = _descriptor(channel)
    auth_err = await check_owned(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    if d.credential_manager_ref and d.has_test:
        from narranexus.platform.module_system.data_access.channel_store import DirectStore

        return await DirectStore().test_connection(channel, body.agent_id)
    record = await GenericCredentialStore(await _db()).get(channel, body.agent_id)
    if record is None:
        return {"success": False, "error": f"no {d.display_name} credential bound for this agent"}
    return {"success": True, "data": {"checked": False, "enabled": record.enabled}}


@router.post("/{channel}/unbind")
@structured_envelope("channels")
async def channel_unbind(request: Request, channel: str, body: AgentBody) -> dict[str, Any]:
    d = _descriptor(channel)
    auth_err = await check_owned(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    db = await _db()
    if d.credential_manager_ref:
        from narranexus.platform.module_system.data_access.channel_store import DirectStore

        result = await DirectStore().unbind(channel, body.agent_id)
        return result
    removed = await GenericCredentialStore(db).unbind(channel, body.agent_id)
    if not removed:
        return {"success": False, "error": f"no {d.display_name} credential bound for this agent"}
    logger.info(f"[channels] {channel} unbound: agent={body.agent_id}")
    return {"success": True, "data": {"unbound": True}}


@router.post("/{channel}/set-active")
@structured_envelope("channels")
async def channel_set_active(request: Request, channel: str, body: SetActiveBody) -> dict[str, Any]:
    d = _descriptor(channel)
    auth_err = await check_owned(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    # Every channel's binding lives in the generic store, so the flag flips there
    # for builtins and plugins alike; the trigger's credential watcher picks the
    # change up on its next poll (a bundle-imported binding goes live here).
    ok = await GenericCredentialStore(await _db()).set_enabled(channel, body.agent_id, body.active)
    if not ok:
        return {"success": False, "error": f"No {d.display_name} credential bound to this agent."}
    logger.info(f"[channels] {channel} {'activated' if body.active else 'deactivated'}: agent={body.agent_id}")
    return {"success": True, "enabled": body.active}


__all__ = ["router"]
