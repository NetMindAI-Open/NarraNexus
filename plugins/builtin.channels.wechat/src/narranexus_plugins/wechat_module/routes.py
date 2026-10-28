"""
@file_name: routes.py
@author:
@date: 2026-06-24
@description: Backend API routes for WeChat (iLink) account binding.

Personal WeChat binds via a QR-scan flow (not a token paste), so unlike
telegram.py the bind is two steps:

  POST /api/wechat/qrcode/start   — get a login QR (qrcode + scannable URL)
  POST /api/wechat/qrcode/poll    — poll scan status; on "confirmed" persist the
                                    iLink bot_token + base_url for the agent

credential / unbind / set-active are the generic ``/api/channels/wechat/…``
routes (batch 4d.3); only the QR flow is WeChat-specific.

``get_qrcode_status`` long-polls on the gateway side; the frontend re-calls
/poll until it returns ``status:"confirmed"`` (or the user cancels).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel, Field

from narranexus_plugins.wechat_module._wechat_credential_manager import (
    WeChatCredentialManager,
)
from narranexus_plugins.wechat_module.wechat_sdk_client import (
    fetch_qrcode,
    poll_qrcode_status,
)


# One canonical owner check (backend/routes/_ownership.py); module-level
# alias keeps the historical local name at its ~per-route call sites. No
# import cycle: this subpackage never gets imported back from _ownership.
from narranexus.sdk.web import check_agent_owner as _verify_agent_ownership

router = APIRouter()

_SAFE_ID_PATTERN = r"^[a-zA-Z0-9_\-]+$"


class AgentRequest(BaseModel):
    agent_id: str = Field(min_length=1, max_length=64, pattern=_SAFE_ID_PATTERN)


class QrStartRequest(AgentRequest):
    pass


class QrPollRequest(AgentRequest):
    qrcode: str = Field(min_length=1, max_length=4096)
    # NOTE: no client-supplied base_url. /qrcode/start never hands one out, so a
    # client could only ever inject one — and the backend fetches it server-side
    # (SSRF: internal hosts / cloud metadata). The host is the fixed iLink
    # default; a genuine per-account baseurl is read from the gateway's own
    # confirm response below, never from the caller.


async def _get_db():
    from narranexus.platform.utils.db.db_factory import get_db_client

    return await get_db_client()


async def _agent_owner_user_id(agent_id: str) -> str:
    """The agent's owner via the canonical resolver (never a hand-rolled
    ``get_one("agents", ...)`` — that's the drift #258 exists to end). A
    failed lookup (None) degrades to "" here: this value labels the binding
    row, it is not an authorization decision."""
    from narranexus.platform.repository import AgentRepository

    db = await _get_db()
    return (await AgentRepository(db).resolve_owner(agent_id)) or ""


@router.post("/qrcode/start")
async def wechat_qrcode_start(request: Request, body: QrStartRequest) -> dict[str, Any]:
    """Begin a bind: fetch a login QR. Returns ``{success, qrcode, qr_url}``.

    ``qr_url`` is a WeChat URL the frontend renders as a scannable QR; ``qrcode``
    is the opaque handle passed back to /qrcode/poll.
    """
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    try:
        res = await fetch_qrcode()
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[wechat:{body.agent_id}] get_bot_qrcode failed: {e}")
        return {"success": False, "error": f"could not reach the WeChat gateway: {e}"}
    if not res.get("qrcode") or not res.get("qr_url"):
        return {"success": False, "error": "gateway returned no QR code"}
    return {"success": True, "data": res}


@router.post("/qrcode/poll")
async def wechat_qrcode_poll(request: Request, body: QrPollRequest) -> dict[str, Any]:
    """Poll the scan status. On ``confirmed`` persist the binding.

    Returns ``{success, status}`` where status is ``wait`` (keep polling) or
    ``confirmed`` (bound — stop polling).
    """
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}

    try:
        status = await poll_qrcode_status(body.qrcode)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[wechat:{body.agent_id}] get_qrcode_status failed: {e}")
        return {"success": False, "error": f"status poll failed: {e}"}

    if status.get("status") != "confirmed":
        # "wait" (still scanning / long-poll expired) — frontend re-polls.
        return {"success": True, "data": {"status": status.get("status", "wait")}}

    bot_token = status.get("bot_token", "")
    # Only the gateway's own confirm response can set a per-account host.
    base_url = status.get("baseurl", "")
    if not bot_token:
        return {"success": False, "error": "gateway confirmed but returned no bot_token"}

    owner_user_id = await _agent_owner_user_id(body.agent_id)
    db = await _get_db()
    mgr = WeChatCredentialManager(db)
    result = await mgr.bind(body.agent_id, bot_token, base_url, owner_user_id)
    if result.get("success"):
        logger.info(f"WeChat account bound: agent={body.agent_id}")
        return {"success": True, "data": {"status": "confirmed"}}
    return result


# ---- plugin contribution (batch 3c.5): this router belongs to builtin.channels.wechat and is
# mounted by backend.plugins_host from backend.routes (no longer included by
# backend/main.py), so its paths 404 together with the plugin when disabled.
from narranexus.contracts.route import RouterSpec  # noqa: E402
from narranexus.kernel.plugins.registry import Contribution  # noqa: E402

ROUTES = (Contribution("channel_wechat", lambda: RouterSpec(router, "/api/wechat", tags=('WeChat',))),)
