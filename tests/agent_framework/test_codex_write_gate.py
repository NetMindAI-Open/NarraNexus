"""
@file_name: test_codex_write_gate.py
@author: NarraNexus
@date: 2026-06-15
@description: Contract tests for the cloud codex write-gate (PR #25 §1/§2).

The gate confines codex writes to the per-agent workspace by routing
per-thread approvals to (on_request, reviewer=None) and cancelling every
out-of-workspace escalation via a client-side approval handler.

These tests do NOT exercise the SDK (no subprocess, no network). They lock:
  * handler logic — cancels the two escalation methods, passes everything
    else (notably MCP) through;
  * _install_write_gate — sets _approval_handler on the AsyncCodex object
    graph, returns False (not raise) when the layout drifts;
  * the openai_codex internal symbols _thread_start_gated imports still exist
    with the shape we pass — an SDK upgrade that moves them fails CI, not
    users.

Live behavior (does `cancel` actually block the write?) is verified by
scripts/spike_codex_approval_probe.py as a manual release gate.
"""
from __future__ import annotations

import types

import pytest

from xyz_agent_context.agent_framework.xyz_codex_official_sdk import (
    _ESCALATION_METHODS,
    _install_write_gate,
    _workspace_write_cancel_handler,
)


# ───────────── handler logic ───────────────────────────────────────────────

@pytest.mark.parametrize("method", list(_ESCALATION_METHODS))
def test_handler_cancels_escalations(method):
    assert _workspace_write_cancel_handler(method, {}) == {"decision": "cancel"}


@pytest.mark.parametrize(
    "method",
    [
        "item/mcpToolCall/started",
        "item/mcpToolCall/requestApproval",  # MCP must NOT be cancelled
        "item/agentMessage/delta",
        "",
    ],
)
def test_handler_passes_non_escalations(method):
    # Anything that is not a filesystem/command escalation falls through to
    # the SDK default ({}), keeping MCP and ordinary items alive.
    assert _workspace_write_cancel_handler(method, {}) == {}


def test_escalation_methods_are_the_two_known_ones():
    assert set(_ESCALATION_METHODS) == {
        "item/commandExecution/requestApproval",
        "item/fileChange/requestApproval",
    }


# ───────────── _install_write_gate ─────────────────────────────────────────

def _fake_codex_with_graph():
    """Stand-in for AsyncCodex._client._sync._approval_handler — no subprocess."""
    sync = types.SimpleNamespace(_approval_handler=None)
    client = types.SimpleNamespace(_sync=sync)
    return types.SimpleNamespace(_client=client), sync


def test_install_sets_handler_and_returns_true():
    codex, sync = _fake_codex_with_graph()
    assert _install_write_gate(codex) is True
    assert sync._approval_handler is _workspace_write_cancel_handler


def test_install_returns_false_when_graph_missing():
    # SDK layout drift (no _client / no _sync) must degrade gracefully, never raise.
    assert _install_write_gate(types.SimpleNamespace()) is False
    assert _install_write_gate(
        types.SimpleNamespace(_client=types.SimpleNamespace())
    ) is False


# ───────────── SDK symbol contract (what _thread_start_gated imports) ───────

def test_sdk_symbols_for_gated_thread_start_exist():
    # If a future openai_codex moves/renames these, _thread_start_gated falls
    # back at runtime — but we want it RED at CI first.
    from openai_codex.api import AsyncThread  # noqa: F401
    from openai_codex._sandbox import _sandbox_mode  # noqa: F401
    from openai_codex.generated.v2_all import (  # noqa: F401
        AskForApproval,
        AskForApprovalValue,
        ThreadStartParams,
    )

    assert hasattr(AskForApprovalValue, "on_request")


def test_thread_start_params_accepts_our_kwargs():
    from openai_codex.generated.v2_all import ThreadStartParams

    fields = ThreadStartParams.model_fields
    for kw in ("approval_policy", "approvals_reviewer", "sandbox"):
        assert kw in fields, f"ThreadStartParams lost {kw!r}"


def test_workspace_write_sandbox_mode_resolves():
    # _thread_start_gated passes _sandbox_mode(Sandbox.workspace_write).
    from openai_codex import Sandbox
    from openai_codex._sandbox import _sandbox_mode

    assert _sandbox_mode(Sandbox.workspace_write) is not None
