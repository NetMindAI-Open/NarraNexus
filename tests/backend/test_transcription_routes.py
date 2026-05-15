"""
@file_name: test_transcription_routes.py
@description: TestClient coverage for the two new routes:
  - GET /api/transcription/availability
  - GET /api/public/transcription/audio/{token}
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.routes import (
    transcription as availability_route,
    transcription_public as public_route,
)
from xyz_agent_context.agent_framework.transcription import (
    service as svc_mod,
    url_signer,
)


# ─────────────────────────────────────────────────────────────────────
# /api/transcription/availability
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture
def availability_app(monkeypatch):
    app = FastAPI()
    app.include_router(availability_route.router, prefix="/api/transcription")
    return app


def _patch_service(monkeypatch, *, available: bool, reason: str):
    fake = MagicMock()
    fake.availability_reason = AsyncMock(return_value=(available, reason))
    fake.is_available = AsyncMock(return_value=available)
    monkeypatch.setattr(
        svc_mod.TranscriptionService, "instance", classmethod(lambda cls: fake),
    )
    return fake


def test_availability_reports_has_openai(availability_app, monkeypatch):
    _patch_service(monkeypatch, available=True, reason="has_openai")
    client = TestClient(availability_app)
    resp = client.get("/api/transcription/availability?user_id=u1")
    assert resp.status_code == 200
    assert resp.json() == {"available": True, "reason": "has_openai"}


def test_availability_reports_none(availability_app, monkeypatch):
    _patch_service(monkeypatch, available=False, reason="none")
    client = TestClient(availability_app)
    resp = client.get("/api/transcription/availability?user_id=u1")
    assert resp.json() == {"available": False, "reason": "none"}


def test_availability_reports_system_free_tier(availability_app, monkeypatch):
    _patch_service(monkeypatch, available=True, reason="system_free_tier")
    client = TestClient(availability_app)
    resp = client.get("/api/transcription/availability?user_id=u1")
    body = resp.json()
    assert body["available"] is True
    assert body["reason"] == "system_free_tier"


def test_availability_reports_none_openai_only(availability_app, monkeypatch):
    """Local / no-public-ingress deployment: dialog drops the NetMind
    branch — frontend reads this reason and renders OpenAI-only copy."""
    _patch_service(monkeypatch, available=False, reason="none_openai_only")
    client = TestClient(availability_app)
    resp = client.get("/api/transcription/availability?user_id=u1")
    body = resp.json()
    assert body["available"] is False
    assert body["reason"] == "none_openai_only"


# ─────────────────────────────────────────────────────────────────────
# /api/public/transcription/audio/{token}
# ─────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _signer_settings(monkeypatch):
    monkeypatch.setattr(url_signer.settings, "transcription_hmac_secret", "test-secret-32B")
    monkeypatch.setattr(url_signer.settings, "public_base_url", "https://my.host")


@pytest.fixture
def public_app(monkeypatch, tmp_path):
    """Wire just the public audio route, with attachment_storage's
    ``resolve_attachment_path`` redirected to a tmp directory we control."""
    storage = tmp_path / "attachments"
    storage.mkdir()

    def _resolve(agent_id, user_id, file_id):
        # Two-tier lookup: original at file_id.<ext>, mp3 cache at
        # file_id.mp3 sibling. The route's _resolve_path_for_variant
        # uses the path's .with_suffix(".mp3") so we just need to
        # return the original path; the route handles cache lookup.
        for candidate in storage.glob(f"{file_id}.*"):
            if candidate.suffix != ".mp3" or "_only_mp3_" not in file_id:
                return candidate
        return None

    monkeypatch.setattr(public_route, "resolve_attachment_path", _resolve)

    app = FastAPI()
    app.include_router(public_route.router, prefix="/api/public/transcription")
    return app, storage


def _make_token(**overrides):
    defaults = dict(
        file_id="att_a1b2c3d4",
        agent_id="ag",
        user_id="u",
        variant="original",
    )
    defaults.update(overrides)
    return url_signer.mint(**defaults)


def test_public_route_serves_original_bytes(public_app):
    app, storage = public_app
    audio_bytes = b"FAKE-MP3-BYTES" * 100
    (storage / "att_a1b2c3d4.mp3").write_bytes(audio_bytes)

    token = _make_token(variant="original")
    client = TestClient(app)
    resp = client.get(f"/api/public/transcription/audio/{token}")

    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("audio/")
    assert resp.content == audio_bytes


def test_public_route_serves_cached_mp3_for_mp3_variant(public_app):
    app, storage = public_app
    # Write the original (.webm) AND the transcoded sibling (.mp3)
    (storage / "att_a1b2c3d4.webm").write_bytes(b"original-webm-bytes")
    cached_bytes = b"CACHED-MP3" * 50
    (storage / "att_a1b2c3d4.mp3").write_bytes(cached_bytes)

    token = _make_token(variant="mp3")
    client = TestClient(app)
    resp = client.get(f"/api/public/transcription/audio/{token}")

    assert resp.status_code == 200
    assert resp.content == cached_bytes
    assert resp.headers["content-type"] == "audio/mpeg"


def test_public_route_returns_404_when_mp3_variant_missing(public_app):
    app, storage = public_app
    # Only the .webm exists — no transcoded mp3 sibling
    (storage / "att_a1b2c3d4.webm").write_bytes(b"webm-only")

    token = _make_token(variant="mp3")
    client = TestClient(app)
    resp = client.get(f"/api/public/transcription/audio/{token}")
    assert resp.status_code == 404


def test_public_route_returns_404_when_orphan(public_app):
    app, _ = public_app
    token = _make_token(file_id="att_aaaaaaaa")
    client = TestClient(app)
    resp = client.get(f"/api/public/transcription/audio/{token}")
    assert resp.status_code == 404


def test_public_route_410_on_expired_token(public_app):
    app, storage = public_app
    (storage / "att_a1b2c3d4.mp3").write_bytes(b"x")
    token = url_signer.mint(
        file_id="att_a1b2c3d4", agent_id="ag", user_id="u",
        variant="original", ttl_seconds=-10,
    )
    client = TestClient(app)
    resp = client.get(f"/api/public/transcription/audio/{token}")
    assert resp.status_code == 410


def test_public_route_401_on_tampered_signature(public_app):
    app, storage = public_app
    (storage / "att_a1b2c3d4.mp3").write_bytes(b"x")
    token = _make_token()
    payload, _ = token.split(".", 1)
    bad_token = f"{payload}.AAAAAAAAAAAA"
    client = TestClient(app)
    resp = client.get(f"/api/public/transcription/audio/{bad_token}")
    assert resp.status_code == 401


def test_public_route_401_on_malformed_token(public_app):
    app, _ = public_app
    client = TestClient(app)
    resp = client.get("/api/public/transcription/audio/notavalidtoken")
    assert resp.status_code == 401
