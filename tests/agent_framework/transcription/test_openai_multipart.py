"""
@file_name: test_openai_multipart.py
@description: OpenAI Whisper multipart backend — happy path, retries, never-raise
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest

from xyz_agent_context.agent_framework.transcription.backends import (
    openai_multipart as M,
)
from xyz_agent_context.agent_framework.transcription.backends.openai_multipart import (
    OpenAIMultipartBackend,
    WHISPER_MAX_FILE_BYTES,
)
from xyz_agent_context.agent_framework.transcription.credential import (
    TranscriptionBackendKind,
    TranscriptionCredential,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _make_audio(tmp_path: Path, suffix: str = ".wav", size: int = 1024) -> Path:
    p = tmp_path / f"sample{suffix}"
    p.write_bytes(b"\x00" * size)
    return p


def _make_cred(base_url: str = "https://api.openai.com/v1") -> TranscriptionCredential:
    return TranscriptionCredential(
        backend_kind=TranscriptionBackendKind.OPENAI_MULTIPART,
        api_key="sk-test",
        base_url=base_url,
        model="whisper-1",
        source_tag="test",
    )


def _patch_httpx(monkeypatch, handler):
    """Replace httpx.AsyncClient with a fake whose .post(...) calls
    handler(url, data, files, headers) and returns its result."""
    class _FakeClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, data=None, files=None, headers=None):
            return await handler(url, data, files, headers)
    monkeypatch.setattr(M.httpx, "AsyncClient", _FakeClient)


def _resp(status_code: int, text: str = "") -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.text = text
    return r


# ─────────────────────────────────────────────────────────────────────
# Tests
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_returns_transcript(tmp_path, monkeypatch):
    audio = _make_audio(tmp_path)

    async def handler(url, data, files, headers):
        assert url == "https://api.openai.com/v1/audio/transcriptions"
        assert data["model"] == "whisper-1"
        assert data["response_format"] == "text"
        assert headers["Authorization"] == "Bearer sk-test"
        assert "file" in files
        return _resp(200, "  hello world  ")

    _patch_httpx(monkeypatch, handler)
    backend = OpenAIMultipartBackend()
    out = await backend.transcribe(
        str(audio), _make_cred(),
        file_id="att_a1b2c3d4", agent_id="a", user_id="u",
    )
    assert out == "hello world"


@pytest.mark.asyncio
async def test_empty_response_returns_none(tmp_path, monkeypatch):
    audio = _make_audio(tmp_path)
    _patch_httpx(monkeypatch, lambda *a: _resp_async(200, ""))

    async def handler(*a):
        return _resp(200, "")

    _patch_httpx(monkeypatch, handler)
    out = await OpenAIMultipartBackend().transcribe(
        str(audio), _make_cred(),
        file_id="att_a1b2c3d4", agent_id="a", user_id="u",
    )
    assert out is None


def _resp_async(*a, **kw):
    """Helper to satisfy older test fixtures expecting a coroutine."""
    raise RuntimeError("use async handler instead")


@pytest.mark.asyncio
async def test_retries_once_on_429_then_succeeds(tmp_path, monkeypatch):
    audio = _make_audio(tmp_path)
    calls = []

    async def handler(url, data, files, headers):
        calls.append(1)
        if len(calls) == 1:
            return _resp(429, "rate limited")
        return _resp(200, "second-try transcript")

    _patch_httpx(monkeypatch, handler)
    out = await OpenAIMultipartBackend().transcribe(
        str(audio), _make_cred(),
        file_id="att_a1b2c3d4", agent_id="a", user_id="u",
    )
    assert out == "second-try transcript"
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_no_retry_on_4xx_other_than_429(tmp_path, monkeypatch):
    audio = _make_audio(tmp_path)
    calls = []

    async def handler(url, data, files, headers):
        calls.append(1)
        return _resp(401, "bad key")

    _patch_httpx(monkeypatch, handler)
    out = await OpenAIMultipartBackend().transcribe(
        str(audio), _make_cred(),
        file_id="att_a1b2c3d4", agent_id="a", user_id="u",
    )
    assert out is None
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_retries_once_on_5xx_then_gives_up(tmp_path, monkeypatch):
    audio = _make_audio(tmp_path)
    calls = []

    async def handler(*a):
        calls.append(1)
        return _resp(503, "down")

    _patch_httpx(monkeypatch, handler)
    out = await OpenAIMultipartBackend().transcribe(
        str(audio), _make_cred(),
        file_id="att_a1b2c3d4", agent_id="a", user_id="u",
    )
    assert out is None
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_http_error_retries_then_returns_none(tmp_path, monkeypatch):
    audio = _make_audio(tmp_path)
    calls = []

    async def handler(*a):
        calls.append(1)
        raise httpx.ConnectError("conn refused")

    _patch_httpx(monkeypatch, handler)
    out = await OpenAIMultipartBackend().transcribe(
        str(audio), _make_cred(),
        file_id="att_a1b2c3d4", agent_id="a", user_id="u",
    )
    assert out is None
    assert len(calls) == 2  # one retry


@pytest.mark.asyncio
async def test_oversize_file_skipped_without_http_call(tmp_path, monkeypatch):
    huge = _make_audio(tmp_path, size=WHISPER_MAX_FILE_BYTES + 1)
    called = []

    async def handler(*a):
        called.append(1)
        return _resp(200, "should not be called")

    _patch_httpx(monkeypatch, handler)
    out = await OpenAIMultipartBackend().transcribe(
        str(huge), _make_cred(),
        file_id="att_a1b2c3d4", agent_id="a", user_id="u",
    )
    assert out is None
    assert called == []


@pytest.mark.asyncio
async def test_zero_byte_file_returns_none(tmp_path, monkeypatch):
    empty = _make_audio(tmp_path, size=0)
    out = await OpenAIMultipartBackend().transcribe(
        str(empty), _make_cred(),
        file_id="att_a1b2c3d4", agent_id="a", user_id="u",
    )
    assert out is None


@pytest.mark.asyncio
async def test_unsupported_extension_returns_none(tmp_path):
    p = tmp_path / "notes.txt"
    p.write_bytes(b"hello")
    out = await OpenAIMultipartBackend().transcribe(
        str(p), _make_cred(),
        file_id="att_a1b2c3d4", agent_id="a", user_id="u",
    )
    assert out is None


@pytest.mark.asyncio
async def test_missing_file_returns_none():
    out = await OpenAIMultipartBackend().transcribe(
        "/no/such/audio.mp3", _make_cred(),
        file_id="att_a1b2c3d4", agent_id="a", user_id="u",
    )
    assert out is None


@pytest.mark.asyncio
async def test_file_handle_reopened_each_attempt(tmp_path, monkeypatch):
    """Regression: reusing the same fp on retry posts 0 bytes. The fake
    handler asserts every call carries non-empty file content."""
    audio = _make_audio(tmp_path, size=4096)
    bodies = []

    async def handler(url, data, files, headers):
        # files["file"] is a 3-tuple (name, fp, mime). Read the fp to
        # confirm it's non-empty on every attempt.
        _, fp, _ = files["file"]
        body = fp.read()
        bodies.append(len(body))
        if len(bodies) == 1:
            return _resp(429, "retry")
        return _resp(200, "ok")

    _patch_httpx(monkeypatch, handler)
    out = await OpenAIMultipartBackend().transcribe(
        str(audio), _make_cred(),
        file_id="att_a1b2c3d4", agent_id="a", user_id="u",
    )
    assert out == "ok"
    assert bodies == [4096, 4096]
