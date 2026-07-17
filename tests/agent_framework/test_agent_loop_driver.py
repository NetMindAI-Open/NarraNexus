"""
@file_name: test_agent_loop_driver.py
@author: Bin Liang
@date: 2026-05-29
@description: Tests for the pluggable agent-loop framework registry (iron
rule #9 — adding a framework must be a registration, not a step_3 edit).
"""

import pytest

from xyz_agent_context.agent_framework import (
    ClaudeAgentSDK,
    available_agent_loop_frameworks,
    get_agent_loop_driver,
    register_agent_loop_driver,
    resolve_framework_name,
)
from xyz_agent_context.agent_framework.agent_loop_driver import _REGISTRY


def test_claude_is_registered_by_default():
    assert "claude_code" in available_agent_loop_frameworks()


def test_default_resolves_to_claude_code(monkeypatch):
    monkeypatch.delenv("AGENT_LOOP_FRAMEWORK", raising=False)
    assert resolve_framework_name() == "claude_code"


def test_env_overrides_default(monkeypatch):
    monkeypatch.setenv("AGENT_LOOP_FRAMEWORK", "MyFramework")
    assert resolve_framework_name() == "myframework"


def test_explicit_arg_wins_over_env(monkeypatch):
    monkeypatch.setenv("AGENT_LOOP_FRAMEWORK", "fromenv")
    assert resolve_framework_name("explicit") == "explicit"


def test_get_claude_driver_returns_claude_sdk():
    driver = get_agent_loop_driver("claude_code", working_path="./")
    assert isinstance(driver, ClaudeAgentSDK)
    assert driver.working_path == "./"


def test_unknown_framework_fails_loud():
    with pytest.raises(ValueError, match="Unknown agent-loop framework"):
        get_agent_loop_driver("does-not-exist")


def test_register_and_resolve_custom_driver():
    class _FakeDriver:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def agent_loop(self, messages, mcp_servers, **kwargs):
            yield {"type": "fake"}

    register_agent_loop_driver("fake-fw", _FakeDriver)
    try:
        driver = get_agent_loop_driver("fake-fw", working_path="/tmp/x")
        assert isinstance(driver, _FakeDriver)
        assert driver.kwargs == {"working_path": "/tmp/x"}
    finally:
        _REGISTRY.pop("fake-fw", None)


def test_registration_is_case_insensitive():
    class _D:
        def __init__(self, **kwargs):
            pass

        async def agent_loop(self, messages, mcp_servers, **kwargs):
            yield {}

    register_agent_loop_driver("UPPER", _D)
    try:
        assert "upper" in available_agent_loop_frameworks()
        assert isinstance(get_agent_loop_driver("upper"), _D)
    finally:
        _REGISTRY.pop("upper", None)
