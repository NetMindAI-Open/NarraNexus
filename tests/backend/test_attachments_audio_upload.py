"""
@file_name: test_attachments_audio_upload.py
@author: NarraNexus
@date: 2026-05-07
@description: Audio upload contract — TranscriptionService wiring + source echo

Strategy
--------
- Build a minimal FastAPI app with just the attachments router
- Bypass libmagic (we set the MIME via Content-Type)
- Redirect attachment storage into ``tmp_path``
- Replace the ``TranscriptionService`` singleton with a mock whose
  ``is_available`` / ``transcribe`` are AsyncMocks the test controls
- Use TestClient for in-process HTTP — no real server needed

Originally this file mocked the deleted ``utils.audio_transcription``
module. After the abstraction landed (see
``2026-05-07-transcription-provider-abstraction-design.md``) the upload
route imports ``agent_framework.transcription.TranscriptionService``,
so the patch target is the singleton instead of two free functions.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes import agents_attachments as attachments_mod
from xyz_agent_context.agent_framework.transcription import service as svc_mod


@pytest.fixture
def upload_app(monkeypatch, tmp_path):
    """FastAPI app exposing only the attachments router, with the
    storage layer redirected to ``tmp_path`` so we don't need workspace
    bootstrap. MIME sniffing is bypassed: we set the MIME via the
    test's `Content-Type` and patch `_sniff_mime_type` to honour it."""
    # Mount the real auth middleware: upload_attachment resolves identity
    # via resolve_current_user_id(request) (local mode: X-User-Id header);
    # the ?user_id= query param is no longer an identity source.
    from backend.auth import auth_middleware

    app = FastAPI()
    app.middleware("http")(auth_middleware)
    app.include_router(attachments_mod.router, prefix="/api/agents")

    def _sniff(file, raw_bytes):
        return file.content_type or "application/octet-stream"

    monkeypatch.setattr(attachments_mod, "_sniff_mime_type", _sniff)

    def _fake_store(agent_id, user_id, *, raw_bytes, original_name, mime_type):
        target = tmp_path / f"att_{abs(hash(original_name)) & 0xffffffff:08x}{Path(original_name).suffix}"
        target.write_bytes(raw_bytes)
        return target.stem, target

    monkeypatch.setattr(attachments_mod, "store_uploaded_attachment", _fake_store)
    return app


@pytest.fixture
def mock_service(monkeypatch):
    """Replace the TranscriptionService singleton for the test's lifespan.
    The returned object has ``is_available`` and ``transcribe`` AsyncMocks
    the test can program; the route gets it via
    ``TranscriptionService.instance()``."""
    fake = MagicMock()
    fake.is_available = AsyncMock(return_value=True)
    fake.transcribe = AsyncMock(return_value="hello world")
    monkeypatch.setattr(svc_mod.TranscriptionService, "instance", classmethod(lambda cls: fake))
    return fake


def _post_audio(
    client,
    mime_type: str = "audio/wav",
    filename: str = "voice.wav",
    *,
    source: str | None = None,
):
    url = "/api/agents/agent_x/attachments"
    if source is not None:
        url += f"?source={source}"
    return client.post(
        url,
        headers={"X-User-Id": "user_y"},
        files={"file": (filename, b"\x00" * 1024, mime_type)},
    )


# ---------------------------------------------------------------------------
# Transcription wiring
# ---------------------------------------------------------------------------


def test_upload_audio_with_provider_returns_transcript(upload_app, mock_service):
    client = TestClient(upload_app)
    resp = _post_audio(client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["mime_type"] == "audio/wav"
    assert body["transcript"] == "hello world"
    assert body["transcription_available"] is True
    assert body["category"] == "media"


def test_upload_audio_no_provider(upload_app, mock_service):
    """is_available=False → transcribe NOT called, transcript=None."""
    mock_service.is_available.return_value = False
    client = TestClient(upload_app)
    resp = _post_audio(client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["transcript"] is None
    assert body["transcription_available"] is False
    mock_service.transcribe.assert_not_called()


def test_upload_audio_transcribe_returns_none(upload_app, mock_service):
    """available=True but transcribe→None → response keeps available=True, transcript=None."""
    mock_service.transcribe.return_value = None
    client = TestClient(upload_app)
    resp = _post_audio(client)

    assert resp.status_code == 200
    body = resp.json()
    assert body["transcript"] is None
    # available STILL True — capability exists, this single call failed
    assert body["transcription_available"] is True


def test_upload_non_audio_no_transcribe_call(upload_app, mock_service):
    """PNG upload → neither availability check nor transcribe called.
    Both transcript fields stay None (not False — that would suggest the
    user lacks transcription capability, which is a separate signal)."""
    client = TestClient(upload_app)
    resp = client.post(
        "/api/agents/agent_x/attachments",
        headers={"X-User-Id": "user_y"},
        files={"file": ("cat.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 100, "image/png")},
    )

    assert resp.status_code == 200
    body = resp.json()
    assert body["mime_type"] == "image/png"
    assert body["category"] == "image"
    assert body["transcript"] is None
    assert body["transcription_available"] is None
    mock_service.is_available.assert_not_called()
    mock_service.transcribe.assert_not_called()


def test_upload_audio_passes_ids_through_to_service(upload_app, mock_service):
    """file_id, agent_id, user_id all reach the service for downstream
    URL signing (NetMind backend)."""
    client = TestClient(upload_app)
    _post_audio(client)

    mock_service.transcribe.assert_called_once()
    kwargs = mock_service.transcribe.call_args.kwargs
    assert kwargs["agent_id"] == "agent_x"
    assert kwargs["user_id"] == "user_y"
    assert isinstance(kwargs["file_id"], str) and kwargs["file_id"]
    assert kwargs["file_path"]


# ---------------------------------------------------------------------------
# source echoing — frontend dispatch hint (agent always gets transcript)
# ---------------------------------------------------------------------------


def test_upload_audio_without_source_still_transcribes(upload_app, mock_service):
    """Paperclip / drag-drop upload still triggers Whisper — `source`
    only affects how the frontend chooses to render the bubble."""
    client = TestClient(upload_app)
    resp = _post_audio(client, source=None)

    body = resp.json()
    assert body["transcript"] == "hello world"
    assert body["transcription_available"] is True
    # Anything other than "recording" normalises to "upload"
    assert body["source"] == "upload"
    mock_service.transcribe.assert_called_once()


def test_upload_audio_source_recording_echoes_back(upload_app, mock_service):
    client = TestClient(upload_app)
    resp = _post_audio(client, source="recording")

    body = resp.json()
    assert body["source"] == "recording"


def test_upload_audio_arbitrary_source_normalises_to_upload(upload_app, mock_service):
    """Defensive: a malformed / future-proofed client could send
    ``source=foo``. Anything that isn't "recording" must be reported
    back as "upload" so the frontend dispatch table stays a strict
    two-state machine."""
    client = TestClient(upload_app)
    resp = _post_audio(client, source="something-else")
    assert resp.json()["source"] == "upload"


def test_upload_non_audio_keeps_source_field_for_consistency(upload_app, mock_service):
    """Non-audio uploads also carry the source echo — the discriminator
    stays meaningful even when the transcription pair is null."""
    client = TestClient(upload_app)
    resp = client.post(
        "/api/agents/agent_x/attachments",
        headers={"X-User-Id": "user_y"},
        files={"file": ("cat.png", b"\x89PNG" + b"\x00" * 100, "image/png")},
    )

    body = resp.json()
    assert body["source"] == "upload"
    assert body["transcript"] is None
    assert body["transcription_available"] is None
