"""
@file_name: test_telegram_sdk_client.py
@date: 2026-05-09
@description: Tests for TelegramSDKClient — the only file in the package
that talks to ``api.telegram.org`` directly.

Why this file exists:
    The wrapper has two contracts:
      (1) hot-path methods raise ``TelegramSDKError`` on failure;
      (2) generic ``api_call`` returns the native ``{ok, result|error}``
          envelope so the ``tg_cli`` MCP tool can pass it through to the
          agent.

    We mock ``aiohttp.ClientSession.post`` via monkeypatch — no real
    network is touched.
"""
from __future__ import annotations

from typing import Any

import aiohttp
import pytest

from xyz_agent_context.module.telegram_module import (
    telegram_sdk_client as sdk_mod,
)
from xyz_agent_context.module.telegram_module.telegram_sdk_client import (
    TelegramSDKClient,
    TelegramSDKError,
)


class _FakePostResponse:
    """Stand-in for the ``async with session.post(...)`` context manager."""

    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def json(self) -> dict:
        return self._payload


class _FakeSession:
    """Replaces ``aiohttp.ClientSession`` for the duration of one test."""

    def __init__(self, *, payload: dict | None = None, raise_exc: Exception | None = None):
        self._payload = payload or {"ok": True, "result": {}}
        self._raise_exc = raise_exc
        self.closed = False
        self.calls: list[tuple[str, dict]] = []

    def __init_subclass__(cls, **kwargs):  # pragma: no cover
        super().__init_subclass__(**kwargs)

    def post(self, url: str, json: dict | None = None) -> _FakePostResponse:
        self.calls.append((url, json or {}))
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakePostResponse(self._payload)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch):
    """Default fake — successful empty result. Tests override
    ``fake_session._payload`` before making the SDK call when they
    need a different envelope."""
    holder: dict[str, _FakeSession] = {}

    def _factory(*_args: Any, **_kwargs: Any) -> _FakeSession:
        session = holder.get("session") or _FakeSession()
        holder["session"] = session
        return session

    monkeypatch.setattr(sdk_mod.aiohttp, "ClientSession", _factory)
    return holder


def _set_payload(holder: dict, payload: dict) -> None:
    holder["session"] = _FakeSession(payload=payload)


def _set_exc(holder: dict, exc: Exception) -> None:
    holder["session"] = _FakeSession(raise_exc=exc)


def test_constructor_rejects_empty_token():
    with pytest.raises(ValueError):
        TelegramSDKClient("")


@pytest.mark.asyncio
async def test_get_me_success(fake_session):
    _set_payload(
        fake_session,
        {"ok": True, "result": {"id": 1001, "username": "acme_bot"}},
    )
    client = TelegramSDKClient("1234:tok")
    out = await client.get_me()
    assert out == {"id": 1001, "username": "acme_bot"}
    await client.close()


@pytest.mark.asyncio
async def test_get_me_raises_on_ok_false(fake_session):
    _set_payload(
        fake_session,
        {"ok": False, "description": "Unauthorized"},
    )
    client = TelegramSDKClient("1234:tok")
    with pytest.raises(TelegramSDKError) as exc:
        await client.get_me()
    assert exc.value.code == "Unauthorized"
    await client.close()


@pytest.mark.asyncio
async def test_send_message_minimal_args(fake_session):
    _set_payload(
        fake_session,
        {"ok": True, "result": {"message_id": 7}},
    )
    client = TelegramSDKClient("1234:tok")
    out = await client.send_message(chat_id="99", text="hi")
    assert out["message_id"] == 7
    # Verify args reached upstream
    session = fake_session["session"]
    url, body = session.calls[0]
    assert url.endswith("/sendMessage")
    assert body == {"chat_id": "99", "text": "hi"}
    await client.close()


@pytest.mark.asyncio
async def test_send_message_with_thread_and_reply(fake_session):
    _set_payload(
        fake_session,
        {"ok": True, "result": {"message_id": 8}},
    )
    client = TelegramSDKClient("1234:tok")
    await client.send_message(
        chat_id="99",
        text="threaded",
        reply_to_message_id="6",
        message_thread_id="555",
    )
    session = fake_session["session"]
    _, body = session.calls[0]
    assert body["reply_to_message_id"] == 6
    assert body["message_thread_id"] == 555
    await client.close()


@pytest.mark.asyncio
async def test_get_updates_returns_result_list(fake_session):
    _set_payload(
        fake_session,
        {
            "ok": True,
            "result": [{"update_id": 1, "message": {"text": "hi"}}],
        },
    )
    client = TelegramSDKClient("1234:tok")
    updates = await client.get_updates(offset=0, timeout=30)
    assert len(updates) == 1
    assert updates[0]["update_id"] == 1
    await client.close()


@pytest.mark.asyncio
async def test_delete_webhook_idempotent(fake_session):
    _set_payload(fake_session, {"ok": True, "result": True})
    client = TelegramSDKClient("1234:tok")
    assert await client.delete_webhook() is True
    await client.close()


@pytest.mark.asyncio
async def test_get_chat_resolves_handle(fake_session):
    _set_payload(
        fake_session,
        {"ok": True, "result": {"id": 555, "first_name": "Bin"}},
    )
    client = TelegramSDKClient("1234:tok")
    out = await client.get_chat("@bin_liang")
    assert out["id"] == 555
    session = fake_session["session"]
    _, body = session.calls[0]
    assert body == {"chat_id": "@bin_liang"}
    await client.close()


@pytest.mark.asyncio
async def test_api_call_wraps_client_error_into_envelope(fake_session):
    """HTTP error → ``{ok: false, error, method}`` envelope (not raise)."""
    _set_exc(fake_session, aiohttp.ClientError("boom"))
    client = TelegramSDKClient("1234:tok")
    out = await client.api_call("sendMessage", {"chat_id": "99", "text": "hi"})

    assert out["ok"] is False
    assert "client_error" in out["error"]
    assert out["method"] == "sendMessage"
    await client.close()


@pytest.mark.asyncio
async def test_close_clears_session(fake_session):
    _set_payload(fake_session, {"ok": True, "result": {}})
    client = TelegramSDKClient("1234:tok")
    await client.api_call("getMe", {})
    session_before = client._session
    assert session_before is not None
    await client.close()
    assert client._session is None


@pytest.mark.asyncio
async def test_async_context_manager_lifecycle(fake_session):
    _set_payload(fake_session, {"ok": True, "result": {"id": 1}})
    async with TelegramSDKClient("1234:tok") as client:
        assert client._session is not None
        out = await client.get_me()
        assert out["id"] == 1
    # After __aexit__ the session is closed/cleared
    assert client._session is None
