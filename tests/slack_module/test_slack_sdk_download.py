"""
@file_name: test_slack_sdk_download.py
@date: 2026-05-21
@description: Phase 1b — Unit tests for SlackSDKClient's two new
attachment methods: ``files_info`` (hydrates a file_id into the
canonical metadata dict) and ``download_url`` (Bearer-auth
stream-download with a per-attachment cap).

We monkey-patch ``aiohttp.ClientSession`` for download_url tests and
the ``AsyncWebClient.files_info`` coroutine for files_info tests. No
real network is touched.

Test matrix:
  - files_info success → returns the inner ``file`` dict
  - files_info SlackApiError → raises SlackSDKError with upstream code
  - download_url success → returns bytes
  - download_url stream-cap exceeded mid-stream → raises ``oversized``
  - download_url HTTP non-2xx → raises ``http_<status>``
  - download_url network error → raises ``client_error:*``
  - download_url Bearer header is actually sent
"""
from __future__ import annotations

from typing import Any

import aiohttp
import pytest
from slack_sdk.errors import SlackApiError

from xyz_agent_context.module.slack_module import slack_sdk_client as sdk_mod
from xyz_agent_context.module.slack_module.slack_sdk_client import (
    SlackSDKClient,
    SlackSDKError,
)


# ────────────────────────────────────────────────────────────────────
# files_info — mock AsyncWebClient
# ────────────────────────────────────────────────────────────────────


class _FakeFilesInfoResp:
    def __init__(self, file: dict):
        self.data = {"ok": True, "file": file}

    def get(self, k, default=None):
        return self.data.get(k, default)


@pytest.mark.asyncio
async def test_files_info_success(monkeypatch) -> None:
    client = SlackSDKClient("xoxb-tok")

    async def _fake_files_info(file: str):
        assert file == "F123"
        return _FakeFilesInfoResp({
            "id": "F123",
            "name": "report.pdf",
            "mimetype": "application/pdf",
            "size": 12345,
            "url_private": "https://files.slack.com/files-pri/T1-F123/report.pdf",
        })

    monkeypatch.setattr(client._client, "files_info", _fake_files_info)

    info = await client.files_info("F123")
    assert info["id"] == "F123"
    assert info["mimetype"] == "application/pdf"
    assert info["url_private"].startswith("https://")


@pytest.mark.asyncio
async def test_files_info_raises_on_api_error(monkeypatch) -> None:
    client = SlackSDKClient("xoxb-tok")

    async def _boom(file: str):
        raise SlackApiError(
            message="file_not_found",
            response={"ok": False, "error": "file_not_found"},
        )

    monkeypatch.setattr(client._client, "files_info", _boom)

    with pytest.raises(SlackSDKError) as exc:
        await client.files_info("F-MISSING")
    assert exc.value.code == "file_not_found"


# ────────────────────────────────────────────────────────────────────
# download_url — fake aiohttp.ClientSession
# ────────────────────────────────────────────────────────────────────


class _FakeGetResponse:
    """Mimic the response object inside ``async with session.get(...) as resp``.

    ``body_chunks`` is iterated by ``resp.content.iter_chunked(N)`` —
    we don't actually re-chunk by N, the real Slack response would
    arrive in roughly-N-sized chunks. For test purposes whatever the
    test sets is what gets yielded.
    """

    def __init__(self, *, body_chunks: list[bytes], status: int = 200):
        self._chunks = body_chunks
        self.status = status

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    @property
    def content(self):  # noqa: D401 — mimic aiohttp attribute
        chunks = self._chunks

        class _Stream:
            async def iter_chunked(self, _n: int):
                for c in chunks:
                    yield c

        return _Stream()


class _FakeSession:
    """Drop-in for aiohttp.ClientSession used by ``download_url``."""

    def __init__(
        self,
        *,
        body_chunks: list[bytes] | None = None,
        status: int = 200,
        raise_exc: Exception | None = None,
    ):
        self._body_chunks = body_chunks or [b""]
        self._status = status
        self._raise_exc = raise_exc
        self.closed = False
        self.last_url: str | None = None
        self.last_headers: dict | None = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        self.closed = True

    def get(self, url: str, *, headers: dict | None = None) -> _FakeGetResponse:
        self.last_url = url
        self.last_headers = headers
        if self._raise_exc is not None:
            raise self._raise_exc
        return _FakeGetResponse(body_chunks=self._body_chunks, status=self._status)


@pytest.fixture
def fake_aiohttp(monkeypatch: pytest.MonkeyPatch):
    """Holder fixture — tests set ``holder["session"] = _FakeSession(...)``
    before calling ``download_url``."""
    holder: dict[str, _FakeSession] = {}

    def _factory(*_a, **_kw) -> _FakeSession:
        s = holder.get("session") or _FakeSession()
        holder["session"] = s
        return s

    monkeypatch.setattr(sdk_mod.aiohttp, "ClientSession", _factory)
    return holder


# ────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_download_url_success_and_bearer_header_sent(fake_aiohttp) -> None:
    """Happy path — body returned + Authorization header carries the token."""
    body = b"PDF-bytes-here"
    fake_aiohttp["session"] = _FakeSession(body_chunks=[body])

    client = SlackSDKClient("xoxb-mytoken")
    data = await client.download_url(
        "https://files.slack.com/files-pri/T1-F123/report.pdf",
        max_bytes=10_000_000,
    )
    assert data == body

    sess = fake_aiohttp["session"]
    assert sess.last_url and sess.last_url.endswith("/report.pdf")
    assert sess.last_headers == {"Authorization": "Bearer xoxb-mytoken"}


@pytest.mark.asyncio
async def test_download_url_stream_cap_exceeded(fake_aiohttp) -> None:
    """Cap enforcement: trips during streaming when accumulated bytes > max_bytes."""
    # Three chunks of 5 bytes each = 15 bytes total; cap at 10 → trip mid-stream
    fake_aiohttp["session"] = _FakeSession(
        body_chunks=[b"AAAAA", b"BBBBB", b"CCCCC"]
    )

    client = SlackSDKClient("xoxb-tok")
    with pytest.raises(SlackSDKError) as exc:
        await client.download_url("https://files.slack.com/x", max_bytes=10)
    assert exc.value.code == "oversized"


@pytest.mark.asyncio
async def test_download_url_http_non_2xx(fake_aiohttp) -> None:
    """410 / 404 from Slack file host wraps to ``http_<status>``."""
    fake_aiohttp["session"] = _FakeSession(body_chunks=[b""], status=410)

    client = SlackSDKClient("xoxb-tok")
    with pytest.raises(SlackSDKError) as exc:
        await client.download_url("https://files.slack.com/gone", max_bytes=1024)
    assert exc.value.code == "http_410"


@pytest.mark.asyncio
async def test_download_url_client_error_wrapped(fake_aiohttp) -> None:
    """aiohttp.ClientError wraps to ``client_error:*`` SlackSDKError."""
    fake_aiohttp["session"] = _FakeSession(
        raise_exc=aiohttp.ClientConnectionError("connection reset")
    )

    client = SlackSDKClient("xoxb-tok")
    with pytest.raises(SlackSDKError) as exc:
        await client.download_url("https://files.slack.com/x", max_bytes=1024)
    assert exc.value.code.startswith("client_error:ClientConnectionError")


@pytest.mark.asyncio
async def test_download_url_exactly_at_cap_succeeds(fake_aiohttp) -> None:
    """Boundary: total == max_bytes is allowed (only strictly greater raises)."""
    body = b"X" * 1024
    fake_aiohttp["session"] = _FakeSession(body_chunks=[body])

    client = SlackSDKClient("xoxb-tok")
    data = await client.download_url(
        "https://files.slack.com/exact", max_bytes=1024
    )
    assert data == body
