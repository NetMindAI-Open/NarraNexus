"""
@file_name: test_driver_contract.py
@date: 2026-07-27
@description: Cross-driver conformance tests for the AgentLoopDriver seam.

Every registered driver (claude_code, codex_cli, plus the remote
wrapper) must present the same surface: runtime-checkable Protocol
conformance, a keyword-only ``streaming`` flag, and the capability
negotiation hook. These tests exist so a divergence (like codex's old
positional ``streaming`` or its silently discarded ``**kwargs``) fails
CI instead of surviving as an implicit inconsistency.
"""
from __future__ import annotations

import inspect

import pytest

from xyz_agent_context.agent_framework.adapters.claude.sdk import ClaudeAgentSDK
from xyz_agent_context.agent_framework.loop.driver import AgentLoopDriver
from xyz_agent_context.agent_framework.loop.remote_driver import (
    RemoteAgentLoopDriver,
)

try:
    from xyz_agent_context.agent_framework.adapters.codex.official_sdk import (
        CodexSDKv2,
    )

    _CODEX_AVAILABLE = True
except ImportError:  # pragma: no cover - optional dependency
    _CODEX_AVAILABLE = False


def _drivers():
    ds = [
        ClaudeAgentSDK(working_path="./"),
        RemoteAgentLoopDriver(
            framework="claude_code",
            working_path="./",
            executor_url="http://127.0.0.1:9",
        ),
    ]
    if _CODEX_AVAILABLE:
        ds.append(CodexSDKv2(working_path="./"))
    return ds


# ---------------- Protocol conformance ------------------------------


def test_all_drivers_satisfy_runtime_checkable_protocol():
    for d in _drivers():
        assert isinstance(d, AgentLoopDriver), type(d).__name__


# ---------------- capabilities() negotiation hook -------------------


def test_all_drivers_expose_capabilities_returning_a_set():
    """The empty negotiation seam: existing drivers declare nothing yet
    (behaviour identical to today); a future driver declares e.g.
    {"steering", "plan", "resume"} and orchestrator/frontend switch
    features on it. No consumer exists yet — this pins the surface."""
    for d in _drivers():
        caps = d.capabilities()
        assert isinstance(caps, set), type(d).__name__
        assert caps == set(), (
            f"{type(d).__name__} declares {caps!r}; existing drivers must "
            "declare nothing until the corresponding feature actually ships"
        )


def test_protocol_declares_capabilities_default():
    assert hasattr(AgentLoopDriver, "capabilities")


# ---------------- signature conformance -----------------------------


@pytest.mark.parametrize(
    "cls",
    [ClaudeAgentSDK, RemoteAgentLoopDriver]
    + ([CodexSDKv2] if _CODEX_AVAILABLE else []),
)
def test_agent_loop_streaming_is_keyword_only(cls):
    """The Protocol marks everything after mcp_servers keyword-only.
    A positionally callable ``streaming`` invites call-site drift."""
    sig = inspect.signature(cls.agent_loop)
    param = sig.parameters["streaming"]
    assert param.kind is inspect.Parameter.KEYWORD_ONLY, (
        f"{cls.__name__}.agent_loop's 'streaming' must be keyword-only "
        f"(got {param.kind})"
    )


# ---------------- @timed instrumentation stays on agent_loop --------


def _timed_classes():
    """Driver classes whose agent_loop carries @timed instrumentation.
    (RemoteAgentLoopDriver is deliberately un-instrumented.)"""
    from xyz_agent_context.agent_framework.adapters.codex.cli_sdk import CodexSDK

    classes = [ClaudeAgentSDK, CodexSDK]
    if _CODEX_AVAILABLE:
        classes.append(CodexSDKv2)
    return classes


def test_agent_loop_keeps_timed_instrumentation():
    """PR #167 review catch: inserting capabilities() between the @timed
    decorator and agent_loop silently moved the latency metric + 15s
    slow-call WARNING onto capabilities(). @timed uses functools.wraps,
    so the wrapper exposes __wrapped__ — pin that agent_loop has it and
    capabilities does NOT."""
    for cls in _timed_classes():
        assert hasattr(cls.agent_loop, "__wrapped__"), (
            f"{cls.__name__}.agent_loop lost its @timed wrapper — the "
            f"llm.*.agent_loop latency metric and slow-call WARNING are gone"
        )
        assert not hasattr(cls.capabilities, "__wrapped__"), (
            f"{cls.__name__}.capabilities is decorated — @timed is "
            f"misplaced and mislabels the metric"
        )


# ---------------- codex must not silently discard kwargs ------------


@pytest.mark.skipif(not _CODEX_AVAILABLE, reason="openai-codex not installed")
@pytest.mark.asyncio
async def test_codex_warns_on_unsupported_kwargs(monkeypatch, caplog):
    """CodexSDKv2 used to ``del kwargs`` outright, silently discarding
    ``disallowed_tools`` — the caller believed the constraint was
    applied. The driver still doesn't implement it, but it must say so
    loudly instead of eating it."""
    import sys

    from loguru import logger as loguru_logger

    # Force the lazy SDK import to fail so the generator raises right
    # after the kwargs handling — no real Codex process is spawned.
    monkeypatch.setitem(sys.modules, "openai_codex", None)

    records: list[str] = []
    sink_id = loguru_logger.add(lambda m: records.append(str(m)), level="WARNING")
    try:
        gen = CodexSDKv2(working_path="./").agent_loop(
            messages=[{"role": "user", "content": "hi"}],
            mcp_servers={},
            disallowed_tools=["WebSearch"],
        )
        with pytest.raises(RuntimeError):
            await gen.__anext__()
    finally:
        loguru_logger.remove(sink_id)

    # Note: assert on the explicit warning text, not the bare kwarg name —
    # the @timed decorator's ERROR traceback dump also contains the kwarg
    # name and would false-positive a looser assertion.
    assert any(
        "ignoring unsupported kwargs" in r and "disallowed_tools" in r
        for r in records
    ), (
        "expected a WARNING naming the ignored kwarg 'disallowed_tools', "
        f"got: {records!r}"
    )
