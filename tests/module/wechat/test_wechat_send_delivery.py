"""
@file_name: test_wechat_send_delivery.py
@author: Bin Liang
@date: 2026-07-03
@description: Delivery-path contract for wechat_send after the 2026-07-03
              silent-drop incident (root cause: missing client_id).

The iLink gateway returns HTTP 200 + empty body for every send, delivered or
not. The real drop rule (proven by controlled probes across two QR sessions):
``client_id`` is the server-side dedup key — without it every send shares one
empty key, so the FIRST message of a login session delivers and every later
one is silently swallowed as a duplicate. The early "emoji kills delivery"
theory was a coincidence and is reverted: with client_id present, non-BMP
emoji deliver and render fine.

Contract under test here:
  1. Payload carries a per-message unique ``client_id``, ``from_user_id``
     and ``base_info.channel_version`` (protocol shape).
  2. Text — emoji included — passes through unmodified.
  3. Send failures are logged, never silently swallowed.
"""

import httpx
import pytest

from xyz_agent_context.module.wechat_module.wechat_context_builder import (
    WeChatContextBuilder,
)
from xyz_agent_context.module.wechat_module._wechat_credential_manager import (
    WeChatCredential,
)
from xyz_agent_context.module.wechat_module.wechat_sdk_client import (
    WeChatSDKClient,
)
from xyz_agent_context.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)


def _client_capturing(bodies: list) -> WeChatSDKClient:
    def handler(req: httpx.Request) -> httpx.Response:
        import json

        bodies.append(json.loads(req.content))
        return httpx.Response(200, json={"ret": 0})

    c = WeChatSDKClient("tok", "https://gw.test")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_send_message_preserves_emoji_on_the_wire():
    """Emoji (astral-plane included) must pass through untouched — with
    client_id in the payload they deliver and render fine (P11 probe,
    2026-07-03). The earlier non-BMP strip was a mis-fix from a
    coincidental correlation and is reverted."""
    bodies: list = []
    c = _client_capturing(bodies)
    ok = await c.send_message("u@im.wechat", "tokctx", "手滑了吧 🍉!")
    await c.aclose()
    assert ok is True
    assert bodies[0]["msg"]["item_list"][0]["text_item"]["text"] == "手滑了吧 🍉!"


@pytest.mark.asyncio
async def test_send_message_payload_has_client_id_and_base_info():
    """The iLink protocol payload requires a client-unique ``client_id`` and a
    ``base_info.channel_version`` block. Without client_id the server dedupes
    every send onto the same (empty) key: the FIRST message of a login session
    delivers and every later one is silently swallowed as a duplicate
    (HTTP 200, empty body) — the 2026-07-03 one-reply-per-session incident,
    reproduced across two QR sessions."""
    bodies: list = []
    c = _client_capturing(bodies)
    assert await c.send_message("u@im.wechat", "tok1", "first") is True
    assert await c.send_message("u@im.wechat", "tok2", "second") is True
    await c.aclose()
    assert len(bodies) == 2
    for body in bodies:
        assert body["base_info"]["channel_version"], "base_info.channel_version missing"
        assert body["msg"]["from_user_id"] == ""
        assert body["msg"]["client_id"], "client_id missing"
    assert bodies[0]["msg"]["client_id"] != bodies[1]["msg"]["client_id"], (
        "client_id must be unique per message — a shared key makes the server "
        "dedupe-drop every send after the first"
    )


@pytest.mark.asyncio
async def test_send_message_chunks_get_distinct_client_ids():
    """Chunked long replies are distinct messages — each chunk needs its own
    client_id or chunks 2+ vanish the same way."""
    bodies: list = []
    c = _client_capturing(bodies)
    from xyz_agent_context.module.wechat_module import wechat_sdk_client as sdk

    long_text = "x" * (sdk.MSG_CHUNK + 10)
    assert await c.send_message("u@im.wechat", "tok", long_text) is True
    await c.aclose()
    assert len(bodies) == 2
    ids = [b["msg"]["client_id"] for b in bodies]
    assert len(set(ids)) == 2


@pytest.mark.asyncio
async def test_send_message_logs_chunk_failure():
    from loguru import logger

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ret": -1})

    c = WeChatSDKClient("tok", "https://gw.test")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    captured: list[str] = []
    sink_id = logger.add(lambda m: captured.append(str(m)), level="WARNING")
    import asyncio as _asyncio

    async def _noop(*_a, **_k):
        return None

    orig_sleep = _asyncio.sleep
    _asyncio.sleep = _noop
    try:
        ok = await c.send_message("u@im.wechat", "tokctx", "hello")
    finally:
        _asyncio.sleep = orig_sleep
        logger.remove(sink_id)
        await c.aclose()
    assert ok is False
    assert any("wechat send" in line.lower() for line in captured), (
        "send failure must be logged, not swallowed (CLAUDE.md lesson #3)"
    )


@pytest.mark.asyncio
async def test_reply_instruction_does_not_ban_emoji():
    msg = ParsedMessage(
        message_id="ctx1",
        chat_id="u@im.wechat",
        sender_id="u@im.wechat",
        sender_name="u",
        content="hi",
        content_type=MessageContentType.TEXT,
        chat_type=ChatType.PRIVATE,
        raw={"context_token": "ctx1"},
    )
    cred = WeChatCredential(
        agent_id="agent_x", bot_token="t", base_url="", enabled=True
    )
    info = await WeChatContextBuilder(
        message=msg, credential=cred, agent_id="agent_x"
    ).get_message_info()
    # The emoji ban was removed with the mis-fix revert — the instruction
    # must not steer the agent away from emoji anymore.
    assert "emoji" not in info["reply_instruction"].lower()
