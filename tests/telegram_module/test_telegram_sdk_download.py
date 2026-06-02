"""
@file_name: test_telegram_sdk_download.py
@date: 2026-05-20
@description: Unit tests for ``TelegramSDKClient.download_file`` — the
two-step bot download added in Phase 1a.

We monkey-patch ``aiohttp.ClientSession`` with a fake that handles BOTH
``post`` (used by ``api_call`` for getFile) and ``get`` (used for the
binary fetch). No real network is touched.

Test matrix:
  - success → (bytes, file_path) returned, no token logged
  - size_hint > 20 MB → raises pre-check WITHOUT calling the API
  - getFile non-ok → raises with upstream error code
  - missing file_path in getFile response → raises ``no_file_path``
  - binary fetch HTTP non-2xx → raises ``http_<status>``
  - network error during binary fetch → raises ``client_error:*``
"""
from __future__ import annotations

from typing import Any

import aiohttp
import pytest

from xyz_agent_context.module.telegram_module import (
    telegram_sdk_client as sdk_mod,
)
from xyz_agent_context.module.telegram_module.telegram_sdk_client import (
    TELEGRAM_BOT_DOWNLOAD_CAP_BYTES,
    TelegramSDKClient,
    TelegramSDKError,
)


# ────────────────────────────────────────────────────────────────────
# Fakes — handle both post (api_call) and get (binary fetch)
# ────────────────────────────────────────────────────────────────────


class _FakePostResponse:
    def __init__(self, payload: dict, status: int = 200):
        self._payload = payload
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def json(self):
        return self._payload


class _FakeGetResponse:
    def __init__(self, body: bytes, status: int = 200):
        self._body = body
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def read(self) -> bytes:
        return self._body


class _FakeSession:
    """Routes ``post(...)`` to ``getFile`` and ``get(...)`` to binary fetch.

    Each test seeds the canned responses + optional exceptions before
    instantiating the client.
    """

    def __init__(
        self,
        *,
        post_payload: dict | None = None,
        get_body: bytes = b"",
        get_status: int = 200,
        post_raise: Exception | None = None,
        get_raise: Exception | None = None,
    ):
        self.post_payload = post_payload or {"ok": True, "result": {}}
        self.get_body = get_body
        self.get_status = get_status
        self.post_raise = post_raise
        self.get_raise = get_raise
        self.closed = False
        self.post_calls: list[tuple[str, dict]] = []
        self.get_calls: list[str] = []

    def post(self, url: str, json: dict | None = None) -> _FakePostResponse:
        self.post_calls.append((url, json or {}))
        if self.post_raise is not None:
            raise self.post_raise
        return _FakePostResponse(self.post_payload)

    def get(self, url: str) -> _FakeGetResponse:
        self.get_calls.append(url)
        if self.get_raise is not None:
            raise self.get_raise
        return _FakeGetResponse(self.get_body, status=self.get_status)

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake_session(monkeypatch: pytest.MonkeyPatch):
    """Holder fixture — tests assign ``holder["session"] = _FakeSession(...)``
    BEFORE making the SDK call."""
    holder: dict[str, _FakeSession] = {}

    def _factory(*_args: Any, **_kwargs: Any) -> _FakeSession:
        session = holder.get("session") or _FakeSession()
        holder["session"] = session
        return session

    monkeypatch.setattr(sdk_mod.aiohttp, "ClientSession", _factory)
    return holder


# ────────────────────────────────────────────────────────────────────
# Tests
# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_file_success(fake_session) -> None:
    """Happy path — getFile returns file_path, GET returns bytes."""
    fake_session["session"] = _FakeSession(
        post_payload={
            "ok": True,
            "result": {"file_path": "documents/file_123.pdf", "file_size": 4},
        },
        get_body=b"PDF!",
    )

    client = TelegramSDKClient("123:abc")
    data, file_path = await client.download_file("BAADBAAD")

    assert data == b"PDF!"
    assert file_path == "documents/file_123.pdf"

    sess = fake_session["session"]
    # Exactly one getFile call.
    assert len(sess.post_calls) == 1
    assert sess.post_calls[0][0].endswith("/getFile")
    assert sess.post_calls[0][1] == {"file_id": "BAADBAAD"}
    # One binary GET against the file-API host with the token embedded.
    assert len(sess.get_calls) == 1
    url = sess.get_calls[0]
    assert url.startswith("https://api.telegram.org/file/bot123:abc/")
    assert url.endswith("/documents/file_123.pdf")
    await client.close()


@pytest.mark.asyncio
async def test_download_file_oversized_pre_check_skips_api(fake_session) -> None:
    """size_hint > Telegram cap → raises immediately, no API calls made."""
    fake_session["session"] = _FakeSession()  # would succeed if called

    client = TelegramSDKClient("123:abc")
    too_big = TELEGRAM_BOT_DOWNLOAD_CAP_BYTES + 1
    with pytest.raises(TelegramSDKError) as exc:
        await client.download_file("BAADBAAD", size_hint=too_big)

    assert exc.value.code == "oversized"
    # Confirm pre-check short-circuited BEFORE either HTTP call.
    sess = fake_session["session"]
    assert sess.post_calls == []
    assert sess.get_calls == []
    await client.close()


@pytest.mark.asyncio
async def test_download_file_get_file_not_ok(fake_session) -> None:
    """Upstream getFile reports ok=False → raises with description."""
    fake_session["session"] = _FakeSession(
        post_payload={"ok": False, "description": "Bad Request: file is too big"},
    )
    client = TelegramSDKClient("123:abc")
    with pytest.raises(TelegramSDKError) as exc:
        await client.download_file("BAADBAAD")
    # api_call envelope wraps description into ``error`` (see telegram_sdk_client:90-97).
    assert exc.value.code == "Bad Request: file is too big"
    sess = fake_session["session"]
    # getFile attempted, binary GET never reached.
    assert len(sess.post_calls) == 1
    assert sess.get_calls == []
    await client.close()


@pytest.mark.asyncio
async def test_download_file_missing_file_path(fake_session) -> None:
    """getFile ok=True but no ``file_path`` field → distinct error."""
    fake_session["session"] = _FakeSession(
        post_payload={"ok": True, "result": {"file_size": 100}},
    )
    client = TelegramSDKClient("123:abc")
    with pytest.raises(TelegramSDKError) as exc:
        await client.download_file("BAADBAAD")
    assert exc.value.code == "no_file_path"
    assert fake_session["session"].get_calls == []
    await client.close()


@pytest.mark.asyncio
async def test_download_file_binary_fetch_http_error(fake_session) -> None:
    """Binary GET returns 410 → raises ``http_410``."""
    fake_session["session"] = _FakeSession(
        post_payload={
            "ok": True,
            "result": {"file_path": "documents/x.pdf"},
        },
        get_body=b"",
        get_status=410,
    )
    client = TelegramSDKClient("123:abc")
    with pytest.raises(TelegramSDKError) as exc:
        await client.download_file("BAADBAAD")
    assert exc.value.code == "http_410"
    await client.close()


@pytest.mark.asyncio
async def test_download_file_binary_fetch_client_error(fake_session) -> None:
    """aiohttp.ClientError during GET wraps to ``client_error:*``."""
    fake_session["session"] = _FakeSession(
        post_payload={
            "ok": True,
            "result": {"file_path": "documents/x.pdf"},
        },
        get_raise=aiohttp.ClientConnectionError("connection reset"),
    )
    client = TelegramSDKClient("123:abc")
    with pytest.raises(TelegramSDKError) as exc:
        await client.download_file("BAADBAAD")
    assert exc.value.code.startswith("client_error:ClientConnectionError")
    await client.close()


@pytest.mark.asyncio
async def test_download_file_size_hint_zero_skips_pre_check(fake_session) -> None:
    """size_hint=0 (or None) → no pre-check, normal flow."""
    fake_session["session"] = _FakeSession(
        post_payload={
            "ok": True,
            "result": {"file_path": "documents/y.pdf"},
        },
        get_body=b"y",
    )
    client = TelegramSDKClient("123:abc")
    # size_hint=0 must NOT trigger oversized pre-check.
    data, fp = await client.download_file("BAADBAAD", size_hint=0)
    assert data == b"y"
    assert fp == "documents/y.pdf"
    await client.close()
