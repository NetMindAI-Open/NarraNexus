"""
@file_name: test_jwt_secret_guard.py
@date: 2026-08-11
@description: assert_jwt_secret_safe — cloud-mode fail-fast on a missing,
placeholder, or too-short JWT signing secret (security audit P0-2). A guessable
signing secret lets anyone forge any user's JWT, so cloud mode refuses to boot;
local mode keeps the default (single trusted user, no JWT required). The guard
reads the single module constant `auth.JWT_SECRET`.
"""
from __future__ import annotations

import pytest

import backend.auth as auth


def test_local_mode_noops_even_with_default(monkeypatch):
    monkeypatch.setattr(auth, "_is_cloud_mode", lambda: False)
    monkeypatch.setattr(auth, "JWT_SECRET", auth._DEFAULT_JWT_SECRET)
    auth.assert_jwt_secret_safe()  # must not raise


def test_cloud_rejects_code_default(monkeypatch):
    monkeypatch.setattr(auth, "_is_cloud_mode", lambda: True)
    monkeypatch.setattr(auth, "JWT_SECRET", auth._DEFAULT_JWT_SECRET)
    with pytest.raises(RuntimeError):
        auth.assert_jwt_secret_safe()


def test_cloud_rejects_changeme_template_placeholder(monkeypatch):
    # The real prod failure mode: copied .env.example, never edited the line.
    monkeypatch.setattr(auth, "_is_cloud_mode", lambda: True)
    monkeypatch.setattr(auth, "JWT_SECRET", "CHANGE_ME_TO_A_RANDOM_64_CHAR_STRING")
    with pytest.raises(RuntimeError):
        auth.assert_jwt_secret_safe()


def test_cloud_rejects_empty_or_whitespace(monkeypatch):
    monkeypatch.setattr(auth, "_is_cloud_mode", lambda: True)
    monkeypatch.setattr(auth, "JWT_SECRET", "   ")
    with pytest.raises(RuntimeError):
        auth.assert_jwt_secret_safe()


def test_cloud_rejects_too_short(monkeypatch):
    monkeypatch.setattr(auth, "_is_cloud_mode", lambda: True)
    monkeypatch.setattr(auth, "JWT_SECRET", "short-secret")  # < 32 chars
    with pytest.raises(RuntimeError):
        auth.assert_jwt_secret_safe()


def test_cloud_accepts_strong_secret(monkeypatch):
    monkeypatch.setattr(auth, "_is_cloud_mode", lambda: True)
    monkeypatch.setattr(auth, "JWT_SECRET", "b" * 64)
    auth.assert_jwt_secret_safe()  # must not raise
