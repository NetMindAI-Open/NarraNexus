"""
@file_name: test_artifact_token_secret.py
@author: Bin Liang
@date: 2026-05-21
@description: Signing-secret resolution for artifact view tokens.

Regression (debug/20260521 bug #2B): on a fresh local install with neither
TRANSCRIPTION_HMAC_SECRET nor admin_secret_key set, minting a view token
raised RuntimeError -> the /view-token route 500'd -> artifacts wouldn't
render. Local mode must derive a stable secret instead; cloud must still
refuse to invent one.
"""
from __future__ import annotations

import pytest

import backend.routes._artifact_token as tok
from xyz_agent_context.utils import deployment_mode


@pytest.fixture
def no_configured_secrets(monkeypatch):
    monkeypatch.setattr(tok.settings, "transcription_hmac_secret", "")
    monkeypatch.setattr(tok.settings, "admin_secret_key", "")


def test_local_mode_derives_stable_secret(monkeypatch, no_configured_secrets):
    monkeypatch.setattr(deployment_mode, "is_cloud_mode", lambda: False)
    # No raise, deterministic, and a mint->verify round-trips.
    s1 = tok._secret()
    s2 = tok._secret()
    assert s1 == s2 == tok._LOCAL_FALLBACK_SECRET
    claims = tok.verify(tok.mint(agent_id="agent_x", artifact_id="art_y"))
    assert claims.agent_id == "agent_x"
    assert claims.artifact_id == "art_y"


def test_cloud_mode_refuses_to_derive(monkeypatch, no_configured_secrets):
    monkeypatch.setattr(deployment_mode, "is_cloud_mode", lambda: True)
    with pytest.raises(RuntimeError, match="cloud mode"):
        tok._secret()


def test_explicit_secret_wins_in_any_mode(monkeypatch):
    monkeypatch.setattr(tok.settings, "transcription_hmac_secret", "explicit-secret")
    monkeypatch.setattr(tok.settings, "admin_secret_key", "")
    monkeypatch.setattr(deployment_mode, "is_cloud_mode", lambda: True)
    assert tok._secret() == b"explicit-secret"


def test_admin_secret_key_fallback_local(monkeypatch):
    monkeypatch.setattr(tok.settings, "transcription_hmac_secret", "")
    monkeypatch.setattr(tok.settings, "admin_secret_key", "admin-key")
    monkeypatch.setattr(deployment_mode, "is_cloud_mode", lambda: False)
    assert tok._secret() == b"admin-key"
