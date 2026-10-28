"""
@file_name: test_step3_identity_stamp.py
@author:
@date: 2026-08-10
@description: step_3 dispatch-time identity token selection (blueprint P1).

Cloud: the broker mints the token at ensure() time and it arrives on
ExecutorEnsureResult. Local: the process self-signs — but ONLY when
NX_MCP_AUTH_MODE != off, so a default local run performs no keygen and no
filesystem writes (iron rule #7).
"""
from __future__ import annotations

import pytest

from narranexus.platform.agent_framework.loop.broker_client import ExecutorEnsureResult
from narranexus.platform.agent_runtime._agent_runtime_steps.step_3_agent_loop import (
    _dispatch_identity_token,
)


@pytest.fixture(autouse=True)
def _fresh_local_issuer():
    """get_local_issuer() is a process-wide singleton that publishes its
    public key only on first use — without a reset, a second self-signing
    test with a different tmp_path would read an empty dir (ordering
    coupling flagged by the pre-push review)."""
    from narranexus.platform.module_system.identity import tokens

    tokens._local_issuer = None
    yield
    tokens._local_issuer = None


def test_cloud_token_from_ensure_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("NX_IDENTITY_KEY_DIR", str(tmp_path))
    ensured = ExecutorEnsureResult(
        url="http://nx-exec-a:8020", cold_started=False, identity_token="tok.broker"
    )
    assert _dispatch_identity_token(ensured, "usr_1") == "tok.broker"
    assert list(tmp_path.iterdir()) == []  # no local keygen when cloud signed


def test_local_off_mode_does_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("NX_IDENTITY_KEY_DIR", str(tmp_path))
    monkeypatch.delenv("NX_MCP_AUTH_MODE", raising=False)
    assert _dispatch_identity_token(None, "usr_1") is None
    assert list(tmp_path.iterdir()) == []  # byte-identical local run


def test_local_audit_mode_self_signs(monkeypatch, tmp_path):
    monkeypatch.setenv("NX_IDENTITY_KEY_DIR", str(tmp_path))
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "audit")
    token = _dispatch_identity_token(None, "usr_1")
    assert token

    from narranexus.platform.module_system.identity.tokens import verify_identity_token

    pub = (tmp_path / "identity_ed25519.pub").read_bytes()
    assert verify_identity_token(token, pub).user_id == "usr_1"


def test_old_broker_without_token_falls_back_like_local(monkeypatch, tmp_path):
    monkeypatch.setenv("NX_IDENTITY_KEY_DIR", str(tmp_path))
    monkeypatch.delenv("NX_MCP_AUTH_MODE", raising=False)
    ensured = ExecutorEnsureResult(url="http://nx-exec-a:8020", cold_started=False)
    assert _dispatch_identity_token(ensured, "usr_1") is None


def test_no_user_id_means_no_token(monkeypatch, tmp_path):
    monkeypatch.setenv("NX_IDENTITY_KEY_DIR", str(tmp_path))
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "audit")
    assert _dispatch_identity_token(None, None) is None
    assert list(tmp_path.iterdir()) == []


def test_cloud_never_self_signs_even_in_audit_mode(monkeypatch, tmp_path):
    """Review Important #1: a cloud broker without a signing key must yield NO
    token — a process-local signature cannot verify against the deploy-mounted
    public key and would pollute the audit window's no-token measurement with
    `invalid` noise (and 401 everything under enforce)."""
    monkeypatch.setenv("NX_IDENTITY_KEY_DIR", str(tmp_path))
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "audit")
    ensured = ExecutorEnsureResult(url="http://nx-exec-a:8020", cold_started=False)
    assert _dispatch_identity_token(ensured, "usr_1") is None
    assert list(tmp_path.iterdir()) == []  # no local keygen leaked into cloud


def test_cloud_mode_without_broker_result_never_self_signs(monkeypatch, tmp_path):
    monkeypatch.setenv("NX_IDENTITY_KEY_DIR", str(tmp_path))
    monkeypatch.setenv("NX_MCP_AUTH_MODE", "audit")
    monkeypatch.setattr(
        "narranexus.platform.utils.deployment_mode.is_cloud_mode", lambda: True
    )
    assert _dispatch_identity_token(None, "usr_1") is None
    assert list(tmp_path.iterdir()) == []
