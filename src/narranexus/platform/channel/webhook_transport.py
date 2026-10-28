"""
@file_name: webhook_transport.py
@author: Bin Liang
@date: 2026-09-04
@description: Webhook transport for IM channels (plugin platform batch 4c): a trigger base whose ``connect()`` is fed by the platform's webhook endpoint instead of a socket, plus the request verification the endpoint applies.

A channel with ``transport="webhook"`` subclasses ``WebhookChannelTriggerBase``
and implements only the parsing half of the trigger contract
(``parse_event`` / ``is_echo`` / ``resolve_sender_name`` /
``create_context_builder``); credentials come from the generic store and
events from the ``channel_webhook_events`` inbox the endpoint
``POST /api/channels/{channel}/webhook/{agent_id}`` fills. Every request is
authenticated with the binding's ``webhook_secret``: either the plain token
in ``X-Webhook-Token`` (or ``?token=``) or an HMAC-SHA256 of the raw body in
``X-Webhook-Signature: sha256=<hex>`` — whichever the sender supports.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import secrets
from typing import Any, AsyncIterator, Mapping, Optional

from loguru import logger

from narranexus.platform.channel.channel_trigger_base import ChannelTriggerBase
from narranexus.platform.channel.webhook_inbox import WebhookInbox

SECRET_FIELD = "webhook_secret"
TOKEN_HEADER = "x-webhook-token"
SIGNATURE_HEADER = "x-webhook-signature"


def new_webhook_secret() -> str:
    return secrets.token_urlsafe(32)


def verify_webhook(secret: str, body: bytes, headers: Mapping[str, str]) -> bool:
    """True when the request proves knowledge of ``secret`` (``X-Webhook-Token`` or HMAC-SHA256 of the raw body in ``X-Webhook-Signature``).

    Header or signature only — a query-string form would land the secret in
    every reverse proxy's access log.
    """
    if not secret:
        return False
    lowered = {k.lower(): v for k, v in headers.items()}
    token = lowered.get(TOKEN_HEADER) or ""
    if token and hmac.compare_digest(token.encode("utf-8"), secret.encode("utf-8")):
        return True  # bytes: a non-ASCII header value is a 401, not a TypeError 500
    signature = lowered.get(SIGNATURE_HEADER, "")
    if signature.startswith("sha256="):
        expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(signature[len("sha256="):].strip().lower(), expected)
    return False


class WebhookChannelTriggerBase(ChannelTriggerBase):
    """A channel trigger driven by the webhook inbox.

    Subclasses set ``channel_name`` / ``brand_display`` / ``working_source``
    and implement ``parse_event``, ``is_echo``, ``resolve_sender_name`` and
    ``create_context_builder``. ``connect()`` and ``load_active_credentials()``
    are provided: credentials are the generic store's active records for the
    channel, events are pulled from the inbox every ``WEBHOOK_POLL_SECONDS``.
    """

    WEBHOOK_POLL_SECONDS: float = 1.0
    WEBHOOK_BATCH: int = 50
    # Claimed inbox rows are an audit trail for this long, then the daily
    # retention sweep (ChannelTriggerBase._run_cleanup) purges them.
    WEBHOOK_EVENT_RETENTION_DAYS: int = 7

    def __init__(self, max_workers: int = 3, **kwargs: Any) -> None:
        # The channels supervisor constructs every trigger as ``cls(max_workers=3)``.
        super().__init__(base_workers=max_workers, **kwargs)

    async def _run_cleanup(self) -> None:
        await super()._run_cleanup()
        db = getattr(self, "_db", None)
        if db is None:
            return
        try:
            from datetime import timedelta

            from narranexus.platform.utils import utc_now

            cutoff = utc_now() - timedelta(days=self.WEBHOOK_EVENT_RETENTION_DAYS)
            purged = await WebhookInbox(db).purge_claimed(self.channel_name, cutoff)
            if purged:
                logger.info(f"{type(self).__name__}: purged {purged} claimed webhook events older than {self.WEBHOOK_EVENT_RETENTION_DAYS} days")
        except Exception as e:  # noqa: BLE001 — retention is best-effort
            logger.warning(f"{type(self).__name__}: webhook inbox purge failed: {e}")

    async def load_active_credentials(self) -> list[Any]:
        from narranexus.platform.channel.credential_store import GenericCredentialStore

        db = getattr(self, "_db", None)
        if db is None:
            return []
        return await GenericCredentialStore(db).list_active(self.channel_name)

    async def connect(self, credential: Any) -> AsyncIterator[dict]:
        db = getattr(self, "_db", None)
        if db is None:
            return
        inbox = WebhookInbox(db)
        agent_id = getattr(credential, "agent_id", "")
        while self.running:
            events = await inbox.pull(self.channel_name, agent_id, limit=self.WEBHOOK_BATCH)
            if not events:
                await asyncio.sleep(self.WEBHOOK_POLL_SECONDS)
                continue
            for event in events:
                yield {**event.payload, "_webhook_event_id": event.id, "_received_at": event.received_at}

    async def handle_webhook(self, request_body: bytes, headers: dict) -> Any:  # pragma: no cover - the endpoint feeds the inbox
        raise NotImplementedError("webhook channels are fed through the platform endpoint, not per-trigger handle_webhook")

    def verify_webhook(self, request_body: bytes, headers: dict, signing_secret: str) -> bool:
        return verify_webhook(signing_secret, request_body, headers)


__all__ = ["SECRET_FIELD", "SIGNATURE_HEADER", "TOKEN_HEADER", "WebhookChannelTriggerBase", "new_webhook_secret", "verify_webhook"]
