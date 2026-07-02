"""
@file_name: wechat.py
@author:
@date: 2026-06-24
@description: Backend API routes for WeChat (iLink) account binding.

Personal WeChat binds via a QR-scan flow (not a token paste), so unlike
telegram.py the bind is two steps:

  POST /api/wechat/qrcode/start   — get a login QR (qrcode + scannable URL)
  POST /api/wechat/qrcode/poll    — poll scan status; on "confirmed" persist the
                                    iLink bot_token + base_url for the agent
  GET  /api/wechat/credential     — sanitized binding view (NO token)
  POST /api/wechat/unbind         — remove the binding

``get_qrcode_status`` long-polls on the gateway side; the frontend re-calls
/poll until it returns ``status:"confirmed"`` (or the user cancels).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from loguru import logger
from pydantic import BaseModel, Field

from xyz_agent_context.module.wechat_module._wechat_credential_manager import (
    WeChatCredentialManager,
)
from xyz_agent_context.module.wechat_module.wechat_sdk_client import (
    fetch_qrcode,
    poll_qrcode_status,
)

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
    from xyz_agent_context.utils.db_factory import get_db_client

    return await get_db_client()


async def _verify_agent_ownership(request: Request, agent_id: str) -> str | None:
    """Mirror of telegram.py — local mode (no auth middleware) leaves
    request.state.user_id unset and every route is effectively unauthenticated."""
    if not hasattr(request.state, "user_id") or not request.state.user_id:
        return None
    user_id = request.state.user_id
    db = await _get_db()
    agent = await db.get_one("agents", {"agent_id": agent_id})
    if not agent:
        return f"Agent {agent_id} not found."
    if agent.get("created_by") != user_id:
        return "Permission denied: you do not own this agent."
    return None


async def _agent_owner_user_id(agent_id: str) -> str:
    db = await _get_db()
    agent = await db.get_one("agents", {"agent_id": agent_id})
    return (agent or {}).get("created_by", "") or ""


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


@router.get("/credential")
async def get_wechat_credential(request: Request, agent_id: str) -> dict[str, Any]:
    """Return the sanitised binding view (NO raw token)."""
    auth_err = await _verify_agent_ownership(request, agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    db = await _get_db()
    mgr = WeChatCredentialManager(db)
    return {"success": True, "data": await mgr.get_public(agent_id)}


@router.post("/unbind")
async def unbind_wechat(request: Request, body: AgentRequest) -> dict[str, Any]:
    """Remove the WeChat binding for an agent."""
    auth_err = await _verify_agent_ownership(request, body.agent_id)
    if auth_err:
        return {"success": False, "error": auth_err}
    db = await _get_db()
    mgr = WeChatCredentialManager(db)
    removed = await mgr.unbind(body.agent_id)
    if not removed:
        return {"success": False, "error": "no WeChat credential bound for this agent"}
    logger.info(f"WeChat account unbound: agent={body.agent_id}")
    return {"success": True, "data": {"unbound": True}}
