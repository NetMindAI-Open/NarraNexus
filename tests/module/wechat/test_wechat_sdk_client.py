"""Unit tests for the WeChat iLink SDK client — the gateway quirks must hold."""
import base64

import httpx
import pytest

from xyz_agent_context.module.wechat_module.wechat_sdk_client import (
    WeChatSDKClient,
    WeChatSDKError,
    extract_text,
    ilink_headers,
)


async def _noop_sleep(*_args, **_kwargs):
    """Swap in for asyncio.sleep so the retry backoff doesn't slow tests."""
    return None


def test_ilink_headers_shape():
    h = ilink_headers("tok123")
    assert h["AuthorizationType"] == "ilink_bot_token"
    assert h["Authorization"] == "Bearer tok123"
    # X-WECHAT-UIN is base64 of a decimal string (a random uint32).
    decoded = base64.b64decode(h["X-WECHAT-UIN"]).decode()
    assert decoded.isdigit()
    # QR-fetch is unauthenticated — no Bearer when token is empty.
    assert "Authorization" not in ilink_headers("")


def test_extract_text_concatenates_item_list():
    msg = {"item_list": [
        {"text_item": {"text": "hello "}},
        {"text_item": {"text": "world"}},
        {"image_item": {}},  # non-text item ignored
    ]}
    assert extract_text(msg) == "hello world"
    assert extract_text({}) == ""


def _client_with(handler) -> WeChatSDKClient:
    c = WeChatSDKClient("tok", "https://gw.test")
    c._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return c


@pytest.mark.asyncio
async def test_get_updates_returns_payload_and_advances():
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/ilink/bot/getupdates"
        # Real healthy schema (captured live 2026-07-16 from 3 prod sessions):
        # {msgs, sync_buf, get_updates_buf} — there is NO `ret` field (errors
        # come as {errcode, errmsg}). The old fabricated `{"ret":0,...}` fixture
        # is exactly what masked the silent-death bug — kept faithful now.
        return httpx.Response(
            200, json={"msgs": [], "sync_buf": "s2", "get_updates_buf": "c2"}
        )

    c = _client_with(handler)
    data = await c.get_updates("c1")
    assert data["get_updates_buf"] == "c2"
    await c.aclose()


@pytest.mark.asyncio
async def test_get_updates_raises_on_app_level_ret():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ret": 1001, "msgs": []})  # session expired

    c = _client_with(handler)
    with pytest.raises(WeChatSDKError) as exc_info:
        await c.get_updates("c1")
    err = exc_info.value
    assert err.code == 1001
    # source="updates" is what the trigger keys on to treat a dead session as a
    # PERMANENT auth failure (disable the credential) rather than reconnecting
    # forever — a getupdates ret!=0 means session expired / bad token.
    assert err.source == "updates"
    # Still a RuntimeError subclass so existing message-based callers hold.
    assert isinstance(err, RuntimeError)
    assert "code=1001" in str(err)
    await c.aclose()


@pytest.mark.asyncio
async def test_get_updates_raises_on_errcode_session_timeout():
    """The real iLink error schema (captured live 2026-07-06): a dead session
    comes back as HTTP 200 ``{"errcode":-14,"errmsg":"session timeout"}`` — via
    ``errcode``, NOT ``ret``. The old code only checked ``ret`` (defaults to 0),
    so a dead session slipped through as an idle empty poll → silent death.
    get_updates must raise on a non-zero ``errcode`` too."""
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"errcode": -14, "errmsg": "session timeout"})

    c = _client_with(handler)
    with pytest.raises(WeChatSDKError) as exc_info:
        await c.get_updates("c1")
    err = exc_info.value
    assert err.code == -14
    assert err.source == "updates"        # → is_permanent_auth_failure → disable
    assert "session timeout" in str(err)  # errmsg carried through
    await c.aclose()


@pytest.mark.asyncio
async def test_send_message_chunks_long_text():
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(req.content)
        seen.append(body["msg"]["item_list"][0]["text_item"]["text"])
        assert body["msg"]["to_user_id"] == "wxA"
        assert body["msg"]["context_token"] == "ctx"
        return httpx.Response(200, json={"ret": 0})

    c = _client_with(handler)
    ok = await c.send_message("wxA", "ctx", "x" * 4500)  # 2000 + 2000 + 500
    assert ok is True
    assert [len(s) for s in seen] == [2000, 2000, 500]
    await c.aclose()


@pytest.mark.asyncio
async def test_send_message_stops_after_chunk_failure(monkeypatch):
    """A chunk that fails both attempts must abort the send — sending later
    chunks would deliver a truncated / out-of-order reply under an ok=False."""
    monkeypatch.setattr(
        "xyz_agent_context.module.wechat_module.wechat_sdk_client.asyncio.sleep",
        _noop_sleep,
    )
    attempts: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(req.content)
        attempts.append(body["msg"]["item_list"][0]["text_item"]["text"])
        return httpx.Response(200, json={"ret": 500})  # app-level send failure

    c = _client_with(handler)
    ok = await c.send_message("wxA", "ctx", "a" * 2000 + "b" * 2000)  # 2 chunks
    assert ok is False
    # First chunk: 1 try + 1 retry = 2 attempts, then STOP. The "bbbb" chunk is
    # never attempted.
    assert attempts == ["a" * 2000, "a" * 2000]
    await c.aclose()
