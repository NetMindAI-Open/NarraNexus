"""
@file_name: manyfold_outbound.py
@author:
@date: 2026-08-10
@description: Managed-reply declaration + Manyfold channel-send client.

Lives in integrations/ with the other external-platform clients
(feedback_client, free_tier/wallet_client): it speaks a foreign wire
contract (bearer auth, camelCase DTO), which is platform semantics, not
a generic utility. ``manyfold_runtime_env()`` is additionally the single
parse of the manyfold runtime triple — the notify webhook
(backend/routes/manyfold/sync.py) delegates here so the two outbound
legs can never diverge on env semantics.

Two related facts live here, shared by the channels inventory
(backend/routes/manyfold/sync.py, which EMITS the declaration to the
platform) and the channel modules' outbound wrappers (which ROUTE sends
according to it):

1. Which providers this deployment declares as agent-managed
   (``NEXUS_MANAGED_REPLY_PROVIDERS`` env, comma-separated). The platform
   mapper defaults mirrored channels to managed-ON, so the inventory emits
   an explicit boolean for every row — this env var is the single fact
   source for both the wire field and the outbound routing.
2. The platform's channel-send endpoint (PR #511): hosted channels reply
   through the platform instead of holding provider credentials in the
   sandbox. Same bearer + runtime identity as the notify webhook; the
   request names a room, never a recipient or a credential (target
   binding is platform-enforced — a room with no inbound history is
   refused with 403).

Never raises: outbound failure surfaces as ``False`` exactly like the
direct-send helpers it substitutes (e.g. wechat's ``send_text_once``),
so call sites keep one error surface regardless of route.
"""
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass
from typing import Literal, Optional

import httpx
from loguru import logger

# Test seam: tests inject an httpx.MockTransport here. Production leaves it
# None and the client uses the default transport.
_transport_for_tests: Optional[httpx.BaseTransport] = None

_SEND_TIMEOUT_S_ENV = "MANYFOLD_CHANNEL_SEND_TIMEOUT_S"
_DEFAULT_SEND_TIMEOUT_S = 15.0


def declared_managed_reply_providers() -> frozenset[str]:
    """Providers this deployment declares as agent-managed.

    Empty (the default) means every channel row is emitted with
    ``agent_managed_reply: false`` — the platform keeps delivering replies
    itself and no outbound is routed through channel-send. Rollout is a
    config change, not a code change.
    """
    raw = os.environ.get("NEXUS_MANAGED_REPLY_PROVIDERS", "")
    return frozenset(
        part.strip().lower() for part in raw.split(",") if part.strip()
    )


def managed_reply_declared(provider: str) -> bool:
    return provider.strip().lower() in declared_managed_reply_providers()


@dataclass(frozen=True)
class ManyfoldRuntimeEnv:
    """The manyfold runtime env. ``token`` + ``runtime_id`` are the
    identity and are required (a partial identity reads as "not a
    manyfold surface"). ``webhook_url`` may be empty: the notify leg
    requires it, but channel-send with an explicit
    ``MANYFOLD_CHANNEL_SEND_URL`` does not — that env var is the escape
    hatch for exactly the case where the webhook URL is absent or
    underivable, so gating it on the webhook would defeat its purpose."""

    webhook_url: str
    token: str
    runtime_id: str


def manyfold_runtime_env() -> Optional[ManyfoldRuntimeEnv]:
    """Single parse of the manyfold runtime env.

    Both outbound legs — the notify webhook and channel-send — resolve
    through here, so a rename / fallback / validation change lands on
    both at once instead of silently skewing one of them. Per-leg
    requirements on ``webhook_url`` stay in the legs.
    """
    token = os.environ.get("MANYFOLD_SYNC_WEBHOOK_TOKEN", "").strip()
    runtime_id = os.environ.get("MANYFOLD_RUNTIME_ID", "").strip()
    if not (token and runtime_id):
        return None
    return ManyfoldRuntimeEnv(
        webhook_url=os.environ.get("MANYFOLD_SYNC_WEBHOOK_URL", "").strip(),
        token=token,
        runtime_id=runtime_id,
    )


@dataclass(frozen=True)
class ChannelSendEnv:
    url: str
    token: str
    runtime_id: str


def channel_send_env() -> Optional[ChannelSendEnv]:
    """Resolve the channel-send endpoint from the manyfold runtime env.

    ``MANYFOLD_CHANNEL_SEND_URL`` wins when set. Otherwise the URL is the
    sibling of the notify webhook (both live on the platform's
    ``/internal/narranexus-sync`` controller), derived by swapping the
    trailing ``/notify`` segment. A webhook URL that doesn't end in
    ``/notify`` is refused rather than guessed at — the caller falls back
    to the direct provider send, which is the safe degradation.
    """
    env = manyfold_runtime_env()
    if env is None:
        # Even an explicit channel-send URL needs the identity pair
        # (token + runtime_id); without it there is nothing to send AS.
        return None
    explicit = os.environ.get("MANYFOLD_CHANNEL_SEND_URL", "").strip()
    if explicit:
        # Escape hatch: works without any webhook URL on purpose — it
        # exists precisely for "derivation impossible / different host".
        return ChannelSendEnv(
            url=explicit, token=env.token, runtime_id=env.runtime_id
        )
    if not env.webhook_url:
        return None
    derived = re.sub(r"/notify/?$", "/channel-send", env.webhook_url)
    if derived == env.webhook_url:
        return None
    return ChannelSendEnv(url=derived, token=env.token, runtime_id=env.runtime_id)


def managed_channel_send_active(provider: str) -> bool:
    """True when outbound for ``provider`` should route via the platform.

    Both halves must hold: this deployment declares the provider managed
    (the same declaration the channels inventory emits), and the manyfold
    runtime env resolves a channel-send endpoint. Local / self-hosted
    deployments have no such env and always send direct.
    """
    return managed_reply_declared(provider) and channel_send_env() is not None


# The three-way outcome exists for one caller decision: may the channel
# module fall back to its direct provider send?
# - "delivered": platform accepted (status sent/queued) — done.
# - "unavailable": the platform NEVER RECEIVED a delivery request (endpoint
#   missing: 404/405, or env unresolvable) — direct fallback cannot double-
#   send, so it is safe. Covers "we flipped managed before the platform
#   shipped/deployed channel-send", which would otherwise swallow every
#   reply silently.
# - "failed": a delivery request reached (or may have reached) the platform
#   — 403 policy refusal, 5xx, timeouts, network errors. NO fallback: 403
#   is the target-binding guard (direct send would bypass it with sandbox
#   credentials), and for timeouts the platform may have processed the
#   request — a direct resend races its retry into a double message.
ChannelSendOutcome = Literal["delivered", "failed", "unavailable"]


async def channel_send(
    *,
    agent_id: str,
    provider: str,
    room_id: str,
    text: str,
    source_message_id: Optional[str] = None,
    attachments: Optional[list[str]] = None,
) -> ChannelSendOutcome:
    """POST one outbound message to the platform's channel-send endpoint.

    ``status`` ``sent``/``queued`` both count as delivered — queued means
    the platform accepted the message and owns retrying the provider leg.
    Never raises; see ``ChannelSendOutcome`` for the semantics of the
    other two outcomes.

    ``attachments`` are workspace-relative paths (the platform reads them
    back out of the agent workspace, mirroring its inbound ingest layout).
    """
    env = channel_send_env()
    if env is None:
        logger.warning(
            f"[manyfold-outbound] channel-send env unresolvable; "
            f"provider={provider} agent={agent_id}"
        )
        return "unavailable"
    body: dict[str, object] = {
        "runtimeId": env.runtime_id,
        "agentId": agent_id,
        "provider": provider,
        "roomId": room_id,
        "text": text,
        # Retry-safe replay key. We send each message once, but the platform
        # dedupes on it if an infra layer ever replays the request.
        "idempotencyKey": f"nx-{uuid.uuid4().hex}",
    }
    if source_message_id:
        body["sourceMessageId"] = source_message_id
    if attachments:
        body["attachments"] = [{"path": p} for p in attachments]
    timeout = float(
        os.environ.get(_SEND_TIMEOUT_S_ENV, str(_DEFAULT_SEND_TIMEOUT_S))
    )
    try:
        async with httpx.AsyncClient(
            timeout=timeout, transport=_transport_for_tests
        ) as client:
            resp = await client.post(
                env.url,
                json=body,
                headers={"Authorization": f"Bearer {env.token}"},
            )
        if resp.status_code in (404, 405):
            # Route doesn't exist on the platform (channel-send not
            # deployed / URL derivation wrong): nothing was delivered,
            # nothing will be — tell the caller the platform leg is absent.
            logger.error(
                f"[manyfold-outbound] channel-send endpoint missing "
                f"(HTTP {resp.status_code}) at {env.url} — platform leg "
                f"unavailable; provider={provider} agent={agent_id}"
            )
            return "unavailable"
        if not (200 <= resp.status_code < 300):
            logger.warning(
                f"[manyfold-outbound] channel-send HTTP {resp.status_code} "
                f"provider={provider} agent={agent_id}: {resp.text[:300]}"
            )
            return "failed"
        status = str(resp.json().get("status", ""))
        if status in ("sent", "queued"):
            return "delivered"
        logger.warning(
            f"[manyfold-outbound] channel-send status={status!r} "
            f"provider={provider} agent={agent_id}"
        )
        return "failed"
    except Exception as e:  # noqa: BLE001 — one error surface, logged
        logger.warning(
            f"[manyfold-outbound] channel-send failed "
            f"({type(e).__name__}: {e}) provider={provider} agent={agent_id}"
        )
        return "failed"
