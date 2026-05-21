"""
@file_name: test_bundle_from_url.py
@author: NarraNexus
@date: 2026-05-18
@description: Tests for POST /api/bundle/import/from-url.

Layered:
  - URL validation tests (scheme, allowlist) — no network involved
  - sha256 mismatch — monkeypatched download writes known bytes
  - happy-path wiring — both download + preflight monkeypatched; verifies
    the endpoint correctly chains them and returns the preflight payload

Streaming / size / timeout internals of `_stream_download` itself are
covered by the manual e2e smoke test (real httpx vs a real upstream).
Mocking httpx transports at the unit level here would be more ceremony
than value for the v1 ship.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


async def _async_return(v):
    return v


def _build(monkeypatch):
    """Mount only the bundle router on a fresh app and stub identity."""
    import backend.routes.bundle as bundle_mod

    async def fake_user_id(request):
        return "test_user"

    monkeypatch.setattr(bundle_mod, "_user_id_for_request", fake_user_id)

    app = FastAPI()
    app.include_router(bundle_mod.router, prefix="/api/bundle")
    return TestClient(app), bundle_mod


# ─── URL validation ─────────────────────────────────────────────────────────


def test_rejects_non_http_scheme(monkeypatch):
    client, _ = _build(monkeypatch)
    r = client.post(
        "/api/bundle/import/from-url",
        json={"url": "file:///etc/passwd"},
    )
    assert r.status_code == 400
    assert "scheme" in r.json()["detail"].lower()


def test_rejects_disallowed_host(monkeypatch):
    monkeypatch.delenv("BUNDLE_FETCH_ALLOWED_HOSTS", raising=False)
    client, _ = _build(monkeypatch)
    r = client.post(
        "/api/bundle/import/from-url",
        json={"url": "http://evil.example.com/foo.nxbundle"},
    )
    assert r.status_code == 403
    assert "allowlist" in r.json()["detail"].lower()


def test_rejects_aws_metadata_ip_by_default(monkeypatch):
    """Classic SSRF target — default allowlist must not include it."""
    monkeypatch.delenv("BUNDLE_FETCH_ALLOWED_HOSTS", raising=False)
    client, _ = _build(monkeypatch)
    r = client.post(
        "/api/bundle/import/from-url",
        json={"url": "http://169.254.169.254/latest/meta-data/iam/security-credentials/"},
    )
    assert r.status_code == 403


def test_default_allowlist_accepts_narra_nexus(monkeypatch):
    """The shipped default must allow the production host without env override."""
    monkeypatch.delenv("BUNDLE_FETCH_ALLOWED_HOSTS", raising=False)
    client, bundle_mod = _build(monkeypatch)

    async def fake_download(url, dst):
        dst.write_bytes(b"x")

    async def fake_preflight(path, user_id):
        return {"preflight_token": "pf_ok", "manifest": {}}

    monkeypatch.setattr(bundle_mod, "_stream_download", fake_download)
    monkeypatch.setattr(bundle_mod, "preflight", fake_preflight)

    r = client.post(
        "/api/bundle/import/from-url",
        json={"url": "https://narra.nexus/templates/x.nxbundle"},
    )
    assert r.status_code == 200


def test_env_override_extends_allowlist(monkeypatch):
    monkeypatch.setenv("BUNDLE_FETCH_ALLOWED_HOSTS", "narra.nexus,localhost")
    client, bundle_mod = _build(monkeypatch)

    async def fake_download(url, dst):
        dst.write_bytes(b"x")

    async def fake_preflight(path, user_id):
        return {"preflight_token": "pf_local", "manifest": {}}

    monkeypatch.setattr(bundle_mod, "_stream_download", fake_download)
    monkeypatch.setattr(bundle_mod, "preflight", fake_preflight)

    r = client.post(
        "/api/bundle/import/from-url",
        json={"url": "http://localhost:3001/templates/x.nxbundle"},
    )
    assert r.status_code == 200


# ─── sha256 verification ────────────────────────────────────────────────────


def test_sha256_mismatch_rejected(monkeypatch):
    monkeypatch.setenv("BUNDLE_FETCH_ALLOWED_HOSTS", "trusted.example.com")
    client, bundle_mod = _build(monkeypatch)

    async def fake_download(url, dst):
        dst.write_bytes(b"hello world")  # sha256 starts with b94d27...

    monkeypatch.setattr(bundle_mod, "_stream_download", fake_download)

    r = client.post(
        "/api/bundle/import/from-url",
        json={
            "url": "https://trusted.example.com/x.nxbundle",
            "expected_sha256": "0" * 64,
        },
    )
    assert r.status_code == 400
    assert "sha256" in r.json()["detail"].lower()


def test_sha256_match_accepted(monkeypatch):
    monkeypatch.setenv("BUNDLE_FETCH_ALLOWED_HOSTS", "trusted.example.com")
    client, bundle_mod = _build(monkeypatch)

    import hashlib
    content = b"hello world"
    correct = hashlib.sha256(content).hexdigest()

    async def fake_download(url, dst):
        dst.write_bytes(content)

    async def fake_preflight(path, user_id):
        return {"preflight_token": "pf_sha_ok", "manifest": {}}

    monkeypatch.setattr(bundle_mod, "_stream_download", fake_download)
    monkeypatch.setattr(bundle_mod, "preflight", fake_preflight)

    r = client.post(
        "/api/bundle/import/from-url",
        json={
            "url": "https://trusted.example.com/x.nxbundle",
            "expected_sha256": correct,
        },
    )
    assert r.status_code == 200
    assert r.json()["preflight_token"] == "pf_sha_ok"


def test_sha256_case_insensitive(monkeypatch):
    monkeypatch.setenv("BUNDLE_FETCH_ALLOWED_HOSTS", "trusted.example.com")
    client, bundle_mod = _build(monkeypatch)

    import hashlib
    content = b"hello world"
    correct_upper = hashlib.sha256(content).hexdigest().upper()

    async def fake_download(url, dst):
        dst.write_bytes(content)

    async def fake_preflight(path, user_id):
        return {"preflight_token": "pf_case", "manifest": {}}

    monkeypatch.setattr(bundle_mod, "_stream_download", fake_download)
    monkeypatch.setattr(bundle_mod, "preflight", fake_preflight)

    r = client.post(
        "/api/bundle/import/from-url",
        json={
            "url": "https://trusted.example.com/x.nxbundle",
            "expected_sha256": correct_upper,
        },
    )
    assert r.status_code == 200


# ─── happy-path wiring (download → preflight) ───────────────────────────────


def test_passes_preflight_result_through(monkeypatch):
    monkeypatch.setenv("BUNDLE_FETCH_ALLOWED_HOSTS", "trusted.example.com")
    client, bundle_mod = _build(monkeypatch)

    captured_path: dict = {}
    captured_user: dict = {}

    async def fake_download(url, dst):
        dst.write_bytes(b"fake-bundle-bytes")

    async def fake_preflight(path, user_id):
        captured_path["v"] = str(path)
        captured_user["v"] = user_id
        return {
            "preflight_token": "pf_passthrough",
            "manifest": {"agents": ["a", "b"]},
            "name_clashes": [],
            "warnings": [],
        }

    monkeypatch.setattr(bundle_mod, "_stream_download", fake_download)
    monkeypatch.setattr(bundle_mod, "preflight", fake_preflight)

    r = client.post(
        "/api/bundle/import/from-url",
        json={"url": "https://trusted.example.com/x.nxbundle"},
    )
    assert r.status_code == 200
    body = r.json()
    # Endpoint forwards the preflight response verbatim
    assert body["preflight_token"] == "pf_passthrough"
    assert body["manifest"]["agents"] == ["a", "b"]
    # And invoked preflight with the staged file + identity
    assert captured_path["v"].endswith(".nxbundle")
    assert captured_user["v"] == "test_user"


def test_preflight_value_error_becomes_400(monkeypatch):
    """When preflight rejects the bundle (e.g. missing manifest.json),
    we map that to 400 rather than 500."""
    monkeypatch.setenv("BUNDLE_FETCH_ALLOWED_HOSTS", "trusted.example.com")
    client, bundle_mod = _build(monkeypatch)

    async def fake_download(url, dst):
        dst.write_bytes(b"not really a zip")

    async def fake_preflight(path, user_id):
        raise ValueError("manifest.json missing in bundle")

    monkeypatch.setattr(bundle_mod, "_stream_download", fake_download)
    monkeypatch.setattr(bundle_mod, "preflight", fake_preflight)

    r = client.post(
        "/api/bundle/import/from-url",
        json={"url": "https://trusted.example.com/x.nxbundle"},
    )
    assert r.status_code == 400
    assert "manifest" in r.json()["detail"].lower()
