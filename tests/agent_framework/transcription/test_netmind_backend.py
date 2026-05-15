"""
@file_name: test_netmind_backend.py
@description: NetMind submit + poll, transcode short-circuit, never-raise

We mock httpx.AsyncClient (the same way other backend tests do) plus
``shutil.which`` and ``asyncio.create_subprocess_exec`` so the
transcode path is deterministic without needing real ffmpeg in CI.
"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import httpx
import pytest

from xyz_agent_context.agent_framework.transcription import url_signer
from xyz_agent_context.agent_framework.transcription.backends import (
    netmind as N,
)
from xyz_agent_context.agent_framework.transcription.backends.netmind import (
    NetMindBackend,
)
from xyz_agent_context.agent_framework.transcription.credential import (
    TranscriptionBackendKind,
    TranscriptionCredential,
)


# ─────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────


def _audio(tmp_path: Path, suffix: str = ".mp3", size: int = 1024) -> Path:
    p = tmp_path / f"voice{suffix}"
    p.write_bytes(b"\x00" * size)
    return p


def _cred() -> TranscriptionCredential:
    return TranscriptionCredential(
        backend_kind=TranscriptionBackendKind.NETMIND,
        api_key="netmind-key",
        base_url="https://api.netmind.ai",
        model="openai/whisper",
        source_tag="test:netmind",
    )


def _resp(status_code: int, json_body: Any = None, text: str = "") -> MagicMock:
    r = MagicMock(spec=httpx.Response)
    r.status_code = status_code
    r.text = text or ""
    if json_body is not None:
        r.json = MagicMock(return_value=json_body)
    else:
        r.json = MagicMock(side_effect=ValueError("no json"))
    return r


def _patch_httpx(monkeypatch, *, post_handler, get_handler):
    class _FakeClient:
        def __init__(self, *a, **kw):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *a):
            return False
        async def post(self, url, json=None, headers=None):
            return await post_handler(url, json, headers)
        async def get(self, url, headers=None):
            return await get_handler(url, headers)
    monkeypatch.setattr(N.httpx, "AsyncClient", _FakeClient)


@pytest.fixture(autouse=True)
def _signer_settings(monkeypatch):
    """Make url_signer happy in every test in this file."""
    monkeypatch.setattr(url_signer.settings, "transcription_hmac_secret", "test-secret-32B")
    monkeypatch.setattr(url_signer.settings, "public_base_url", "https://my.host")


@pytest.fixture
def fast_poll(monkeypatch):
    """Make the polling loop tight enough to fit in a test timeout."""
    monkeypatch.setattr(N, "_POLL_INTERVAL_S", 0.0)


# ─────────────────────────────────────────────────────────────────────
# Submit + poll (no transcode)
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_happy_path_submit_then_poll(tmp_path, monkeypatch, fast_poll):
    audio = _audio(tmp_path, ".mp3")
    polls = []

    async def post_handler(url, body, headers):
        assert url == "https://api.netmind.ai/v1/generation"
        assert body["model"] == "openai/whisper"
        assert body["config"]["task"] == "transcribe"
        assert body["config"]["audio_url"].startswith("https://my.host/api/public/")
        assert headers["Authorization"] == "Bearer netmind-key"
        return _resp(200, json_body={"id": "job-xyz", "status": "pending"})

    async def get_handler(url, headers):
        polls.append(url)
        if len(polls) == 1:
            return _resp(200, json_body={"id": "job-xyz", "status": "pending"})
        if len(polls) == 2:
            return _resp(200, json_body={"id": "job-xyz", "status": "initializing"})
        return _resp(200, json_body={
            "id": "job-xyz",
            "status": "completed",
            "result": {"data": [{"text": "hello world"}]},
        })

    _patch_httpx(monkeypatch, post_handler=post_handler, get_handler=get_handler)
    out = await NetMindBackend().transcribe(
        str(audio), _cred(),
        file_id="att_a1b2c3d4", agent_id="ag", user_id="u",
    )
    assert out == "hello world"
    assert len(polls) == 3


@pytest.mark.asyncio
async def test_failed_status_returns_none(tmp_path, monkeypatch, fast_poll):
    audio = _audio(tmp_path, ".mp3")

    async def post_handler(*a):
        return _resp(200, json_body={"id": "job-xyz", "status": "pending"})

    async def get_handler(*a):
        return _resp(200, json_body={
            "id": "job-xyz",
            "status": "failed",
            "logs": [{"text": "Soundfile not in correct format"}],
        })

    _patch_httpx(monkeypatch, post_handler=post_handler, get_handler=get_handler)
    out = await NetMindBackend().transcribe(
        str(audio), _cred(),
        file_id="att_a1b2c3d4", agent_id="ag", user_id="u",
    )
    assert out is None


@pytest.mark.asyncio
async def test_overall_timeout_returns_none(tmp_path, monkeypatch):
    audio = _audio(tmp_path, ".mp3")
    monkeypatch.setattr(N, "_POLL_INTERVAL_S", 0.0)
    monkeypatch.setattr(N, "_OVERALL_TIMEOUT_S", 0.05)

    async def post_handler(*a):
        return _resp(200, json_body={"id": "job-xyz", "status": "pending"})

    async def get_handler(*a):
        # Stays pending forever
        return _resp(200, json_body={"id": "job-xyz", "status": "pending"})

    _patch_httpx(monkeypatch, post_handler=post_handler, get_handler=get_handler)
    out = await NetMindBackend().transcribe(
        str(audio), _cred(),
        file_id="att_a1b2c3d4", agent_id="ag", user_id="u",
    )
    assert out is None


@pytest.mark.asyncio
async def test_submit_non_200_returns_none(tmp_path, monkeypatch, fast_poll):
    audio = _audio(tmp_path, ".mp3")

    async def post_handler(*a):
        return _resp(401, text="bad key")

    async def get_handler(*a):
        pytest.fail("should not have polled after submit failure")

    _patch_httpx(monkeypatch, post_handler=post_handler, get_handler=get_handler)
    out = await NetMindBackend().transcribe(
        str(audio), _cred(),
        file_id="att_a1b2c3d4", agent_id="ag", user_id="u",
    )
    assert out is None


@pytest.mark.asyncio
async def test_missing_transcript_field_returns_none(tmp_path, monkeypatch, fast_poll):
    audio = _audio(tmp_path, ".mp3")

    async def post_handler(*a):
        return _resp(200, json_body={"id": "job-xyz", "status": "pending"})

    async def get_handler(*a):
        # Job completed but result.data is missing
        return _resp(200, json_body={"id": "job-xyz", "status": "completed", "result": {}})

    _patch_httpx(monkeypatch, post_handler=post_handler, get_handler=get_handler)
    out = await NetMindBackend().transcribe(
        str(audio), _cred(),
        file_id="att_a1b2c3d4", agent_id="ag", user_id="u",
    )
    assert out is None


# ─────────────────────────────────────────────────────────────────────
# Transcode dispatch
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_native_extensions_skip_transcode(tmp_path, monkeypatch, fast_poll):
    """mp3/wav/flac/ogg/oga/aiff: variant=original, no ffmpeg call."""
    audio = _audio(tmp_path, ".mp3")
    submitted_url = []

    async def post_handler(url, body, headers):
        submitted_url.append(body["config"]["audio_url"])
        return _resp(200, json_body={"id": "job-xyz", "status": "pending"})

    async def get_handler(*a):
        return _resp(200, json_body={
            "id": "job-xyz", "status": "completed",
            "result": {"data": [{"text": "ok"}]},
        })

    monkeypatch.setattr(
        "shutil.which", lambda _: pytest.fail("ffmpeg should not be probed for mp3"),
    )
    _patch_httpx(monkeypatch, post_handler=post_handler, get_handler=get_handler)
    await NetMindBackend().transcribe(
        str(audio), _cred(),
        file_id="att_a1b2c3d4", agent_id="ag", user_id="u",
    )

    # Decode the token at the end of the URL and inspect its variant.
    token = submitted_url[0].rsplit("/", 1)[-1]
    claims = url_signer.verify(token)
    assert claims.variant == "original"


@pytest.mark.asyncio
async def test_webm_triggers_transcode_and_uses_mp3_variant(tmp_path, monkeypatch, fast_poll):
    audio = _audio(tmp_path, ".webm")
    cached_mp3 = audio.with_suffix(".mp3")

    transcode_calls: list[Any] = []

    async def fake_ffmpeg_to_mp3(src: Path, dst: Path):
        transcode_calls.append((src, dst))
        dst.write_bytes(b"fake-mp3-bytes" * 100)

    monkeypatch.setattr(N, "_ffmpeg_to_mp3", fake_ffmpeg_to_mp3)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")

    submitted_url = []

    async def post_handler(url, body, headers):
        submitted_url.append(body["config"]["audio_url"])
        return _resp(200, json_body={"id": "job-xyz", "status": "pending"})

    async def get_handler(*a):
        return _resp(200, json_body={
            "id": "job-xyz", "status": "completed",
            "result": {"data": [{"text": "decoded"}]},
        })

    _patch_httpx(monkeypatch, post_handler=post_handler, get_handler=get_handler)
    out = await NetMindBackend().transcribe(
        str(audio), _cred(),
        file_id="att_a1b2c3d4", agent_id="ag", user_id="u",
    )
    assert out == "decoded"
    assert len(transcode_calls) == 1
    assert cached_mp3.exists()

    token = submitted_url[0].rsplit("/", 1)[-1]
    claims = url_signer.verify(token)
    assert claims.variant == "mp3"
    assert claims.file_id == "att_a1b2c3d4"


@pytest.mark.asyncio
async def test_webm_reuses_cached_mp3(tmp_path, monkeypatch, fast_poll):
    """A pre-existing {file_id}.mp3 cache file means we don't re-transcode."""
    audio = _audio(tmp_path, ".webm")
    cached = audio.with_suffix(".mp3")
    cached.write_bytes(b"already-transcoded")

    async def fake_ffmpeg_to_mp3(*a):
        pytest.fail("should not have re-transcoded — cache exists")

    monkeypatch.setattr(N, "_ffmpeg_to_mp3", fake_ffmpeg_to_mp3)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")

    async def post_handler(*a):
        return _resp(200, json_body={"id": "j", "status": "pending"})

    async def get_handler(*a):
        return _resp(200, json_body={
            "id": "j", "status": "completed",
            "result": {"data": [{"text": "cached path"}]},
        })

    _patch_httpx(monkeypatch, post_handler=post_handler, get_handler=get_handler)
    out = await NetMindBackend().transcribe(
        str(audio), _cred(),
        file_id="att_a1b2c3d4", agent_id="ag", user_id="u",
    )
    assert out == "cached path"


@pytest.mark.asyncio
async def test_webm_without_ffmpeg_returns_none(tmp_path, monkeypatch):
    audio = _audio(tmp_path, ".webm")

    async def post_handler(*a):
        pytest.fail("must not submit without a successful transcode")

    async def get_handler(*a):
        pytest.fail("must not poll without a submit")

    monkeypatch.setattr("shutil.which", lambda _: None)
    _patch_httpx(monkeypatch, post_handler=post_handler, get_handler=get_handler)
    out = await NetMindBackend().transcribe(
        str(audio), _cred(),
        file_id="att_a1b2c3d4", agent_id="ag", user_id="u",
    )
    assert out is None


@pytest.mark.asyncio
async def test_transcode_failure_cleans_up_partial_cache(tmp_path, monkeypatch):
    audio = _audio(tmp_path, ".webm")
    cached = audio.with_suffix(".mp3")

    async def fake_ffmpeg_to_mp3(src: Path, dst: Path):
        # Simulate a partial write before crash
        dst.write_bytes(b"")
        raise RuntimeError("ffmpeg crashed")

    monkeypatch.setattr(N, "_ffmpeg_to_mp3", fake_ffmpeg_to_mp3)
    monkeypatch.setattr("shutil.which", lambda _: "/usr/bin/ffmpeg")

    async def post_handler(*a):
        pytest.fail("must not submit when transcode failed")

    async def get_handler(*a):
        pytest.fail("must not poll when transcode failed")

    _patch_httpx(monkeypatch, post_handler=post_handler, get_handler=get_handler)
    out = await NetMindBackend().transcribe(
        str(audio), _cred(),
        file_id="att_a1b2c3d4", agent_id="ag", user_id="u",
    )
    assert out is None
    assert not cached.exists()


# ─────────────────────────────────────────────────────────────────────
# URL signing failure
# ─────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_no_public_base_url_returns_none(tmp_path, monkeypatch, fast_poll):
    """Resolver should normally have skipped this credential, but if we
    end up here without public_base_url we must degrade silently rather
    than minting a useless URL."""
    audio = _audio(tmp_path, ".mp3")
    monkeypatch.setattr(url_signer.settings, "public_base_url", "")

    async def post_handler(*a):
        pytest.fail("must not submit when public URL can't be minted")

    async def get_handler(*a):
        pytest.fail("must not poll")

    _patch_httpx(monkeypatch, post_handler=post_handler, get_handler=get_handler)
    out = await NetMindBackend().transcribe(
        str(audio), _cred(),
        file_id="att_a1b2c3d4", agent_id="ag", user_id="u",
    )
    assert out is None
