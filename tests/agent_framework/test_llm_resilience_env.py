"""
@file_name: test_llm_resilience_env.py
@author: Bin Liang
@date: 2026-05-22
@description: #7 — env-tunable LLM resilience knobs.

`to_cli_env()` must inject the per-request timeout + CLI retry count (from
settings) into the Claude Code CLI subprocess env, and the provider health
probe must classify reachability for the stall diagnostic.
"""
import pytest

from xyz_agent_context.agent_framework.api_config import ClaudeConfig
from xyz_agent_context.agent_framework.xyz_claude_agent_sdk import (
    _probe_provider_reachable,
)
from xyz_agent_context.settings import settings


def test_to_cli_env_injects_timeout_and_retries():
    cfg = ClaudeConfig(api_key="k", base_url="https://api.netmind.ai", auth_type="bearer_token")
    env = cfg.to_cli_env()
    assert env["API_TIMEOUT_MS"] == str(settings.llm_api_timeout_ms)
    assert env["CLAUDE_CODE_MAX_RETRIES"] == str(settings.llm_max_retries)


def test_to_cli_env_timeout_tracks_settings(monkeypatch):
    monkeypatch.setattr(settings, "llm_api_timeout_ms", 1234567, raising=False)
    monkeypatch.setattr(settings, "llm_max_retries", 7, raising=False)
    env = ClaudeConfig(api_key="k").to_cli_env()
    assert env["API_TIMEOUT_MS"] == "1234567"
    assert env["CLAUDE_CODE_MAX_RETRIES"] == "7"


def test_resilience_defaults():
    # 10 min per-request bound is generous enough not to cut a legitimately
    # streaming long-thinking pass (铁律 #14), but bounds a pathological hang.
    assert settings.llm_api_timeout_ms == 600000
    assert settings.llm_max_retries == 10


@pytest.mark.asyncio
async def test_probe_none_when_no_base_url():
    assert await _probe_provider_reachable("", 1) is None
    assert await _probe_provider_reachable(None, 1) is None


@pytest.mark.asyncio
async def test_probe_unreachable_returns_false():
    # 127.0.0.1:1 is reserved/refused → fast deterministic connection failure.
    assert await _probe_provider_reachable("http://127.0.0.1:1", 2) is False
