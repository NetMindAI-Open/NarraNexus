"""
@file_name: test_helper_json_repair.py
@date: 2026-07-21
@description: Regression tests for the Claude helper structured-output
    robustness fixes (Lark bug recvoLQb0aTqrE #2).

Both Claude helper SDKs use PROMPT-ENGINEERED structured output (schema in the
prompt + client-side JSON extraction), so a complex nested schema on a cheap
model (Haiku / CLI one-shot) sometimes comes back wrapped in prose the first
try. Before the fix a single unparseable reply threw immediately, and the
Instance-Decision caller silently fell back to a default that DROPPED the
user's requested Job ("success but no Job in tab").

Covers:
  * AnthropicHelperSDK / CliHelperSDK re-prompt for valid JSON up to
    helper_json_repair_attempts times, then raise if still unparseable.
  * CliHelperSDK._run_claude_oneshot bounds the subprocess: helper-scoped
    API_TIMEOUT_MS / CLAUDE_CODE_MAX_RETRIES (not the agent-loop 10min×10),
    and a wall-clock timeout so a stalled subprocess can't hang forever
    ("Job stuck at 正在创建").
"""
from __future__ import annotations

import asyncio

import pytest
from pydantic import BaseModel

import claude_agent_sdk as cas
from xyz_agent_context.agent_framework.api_config import (
    AnthropicHelperConfig,
    ClaudeConfig,
    CliHelperConfig,
    OpenAIConfig,
    set_user_config,
)
from xyz_agent_context.agent_framework.anthropic_helper_sdk import AnthropicHelperSDK
from xyz_agent_context.agent_framework.cli_helper_sdk import CliHelperSDK
from xyz_agent_context.settings import settings


class _Val(BaseModel):
    value: int


# ---------------------------------------------------------------------------
# Fakes for the Anthropic Messages-API seam (AnthropicHelperSDK._build_client)
# ---------------------------------------------------------------------------

class _Blk:
    type = "text"

    def __init__(self, text: str):
        self.text = text


class _Usage:
    input_tokens = 0
    output_tokens = 0


class _Msg:
    def __init__(self, text: str):
        self.content = [_Blk(text)]
        self.usage = _Usage()


class _FakeStream:
    def __init__(self, msg: _Msg):
        self._msg = msg

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def get_final_message(self) -> _Msg:
        return self._msg


class _FakeMessages:
    def __init__(self, replies: list[str]):
        self._replies = list(replies)
        self.calls: list[dict] = []

    def stream(self, **kwargs):
        self.calls.append(kwargs)
        return _FakeStream(_Msg(self._replies.pop(0)))


class _FakeClient:
    def __init__(self, replies: list[str]):
        self.messages = _FakeMessages(replies)


# ---------------------------------------------------------------------------
# AnthropicHelperSDK repair retry
# ---------------------------------------------------------------------------

async def test_anthropic_repairs_on_second_attempt(monkeypatch):
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        anthropic_helper=AnthropicHelperConfig(api_key="k", model="claude-haiku-4-5"),
    )
    fake = _FakeClient(["here is your answer, no json at all", '{"value": 42}'])
    monkeypatch.setattr(AnthropicHelperSDK, "_build_client", staticmethod(lambda: fake))

    result = await AnthropicHelperSDK().llm_function(
        instructions="decide", user_input="go", output_type=_Val,
    )
    assert result.final_output.value == 42
    # Two calls: the garbage reply + the repair re-prompt.
    assert len(fake.messages.calls) == 2
    # The repair turn carries the prior bad reply + a repair instruction.
    assert len(fake.messages.calls[1]["messages"]) == 3


async def test_anthropic_empty_reply_repair_turn_has_nonempty_content(monkeypatch):
    """A turn with NO text block → raw_content "" must not become an empty
    assistant content block (Messages API 400s on that). The repair turn
    substitutes a placeholder and still recovers."""
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        anthropic_helper=AnthropicHelperConfig(api_key="k", model="claude-haiku-4-5"),
    )
    fake = _FakeClient(["", '{"value": 5}'])
    monkeypatch.setattr(AnthropicHelperSDK, "_build_client", staticmethod(lambda: fake))

    result = await AnthropicHelperSDK().llm_function(
        instructions="decide", user_input="go", output_type=_Val,
    )
    assert result.final_output.value == 5
    # The repair turn's assistant content is the placeholder, never "".
    repair_msgs = fake.messages.calls[1]["messages"]
    assistant = next(m for m in repair_msgs if m["role"] == "assistant")
    assert assistant["content"].strip() != ""


async def test_anthropic_raises_after_exhausting_attempts(monkeypatch):
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        anthropic_helper=AnthropicHelperConfig(api_key="k", model="claude-haiku-4-5"),
    )
    monkeypatch.setattr(settings, "helper_json_repair_attempts", 3)
    fake = _FakeClient(["nope"] * 3)
    monkeypatch.setattr(AnthropicHelperSDK, "_build_client", staticmethod(lambda: fake))

    with pytest.raises(ValueError):
        await AnthropicHelperSDK().llm_function(
            instructions="decide", user_input="go", output_type=_Val,
        )
    assert len(fake.messages.calls) == 3


# ---------------------------------------------------------------------------
# CliHelperSDK repair retry (mock the _run_oneshot seam)
# ---------------------------------------------------------------------------

async def test_cli_repairs_on_third_attempt(monkeypatch):
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        cli_helper=CliHelperConfig(framework="claude_code", model="haiku"),
    )
    monkeypatch.setattr(settings, "helper_json_repair_attempts", 3)
    replies = iter([
        ("prose, no json", 0, 0),
        ("```\nstill not valid\n```", 0, 0),
        ('{"value": 7}', 0, 0),
    ])

    async def fake_run_oneshot(system_prompt, user_input, model_name):
        return next(replies)

    sdk = CliHelperSDK()
    monkeypatch.setattr(sdk, "_run_oneshot", fake_run_oneshot)

    result = await sdk.llm_function(
        instructions="decide", user_input="go", output_type=_Val,
    )
    assert result.final_output.value == 7


async def test_cli_repair_prompt_feeds_back_previous_reply(monkeypatch):
    """CLI one-shots are stateless (fresh subprocess per turn), so the repair
    prompt must carry the prior bad reply inline — otherwise json_repair_note's
    'previous response' reference dangles."""
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        cli_helper=CliHelperConfig(framework="claude_code", model="haiku"),
    )
    monkeypatch.setattr(settings, "helper_json_repair_attempts", 2)
    seen_prompts: list[str] = []
    replies = iter([("BAD_REPLY_XYZ not json", 0, 0), ('{"value": 9}', 0, 0)])

    async def fake_run_oneshot(system_prompt, user_input, model_name):
        seen_prompts.append(user_input)
        return next(replies)

    sdk = CliHelperSDK()
    monkeypatch.setattr(sdk, "_run_oneshot", fake_run_oneshot)

    result = await sdk.llm_function(
        instructions="decide", user_input="ORIGINAL_INPUT", output_type=_Val,
    )
    assert result.final_output.value == 9
    # Second (repair) prompt echoes both the original input and the bad reply.
    assert "ORIGINAL_INPUT" in seen_prompts[1]
    assert "BAD_REPLY_XYZ" in seen_prompts[1]


async def test_cli_raises_after_exhausting_attempts(monkeypatch):
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        cli_helper=CliHelperConfig(framework="claude_code", model="haiku"),
    )
    monkeypatch.setattr(settings, "helper_json_repair_attempts", 2)
    calls = {"n": 0}

    async def fake_run_oneshot(system_prompt, user_input, model_name):
        calls["n"] += 1
        return ("never valid json", 0, 0)

    sdk = CliHelperSDK()
    monkeypatch.setattr(sdk, "_run_oneshot", fake_run_oneshot)

    with pytest.raises(ValueError):
        await sdk.llm_function(
            instructions="decide", user_input="go", output_type=_Val,
        )
    assert calls["n"] == 2


async def test_cli_no_schema_single_call(monkeypatch):
    """No output_type → plain completion, one call, no repair loop."""
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        cli_helper=CliHelperConfig(framework="claude_code", model="haiku"),
    )
    calls = {"n": 0}

    async def fake_run_oneshot(system_prompt, user_input, model_name):
        calls["n"] += 1
        return ("plain reply", 0, 0)

    sdk = CliHelperSDK()
    monkeypatch.setattr(sdk, "_run_oneshot", fake_run_oneshot)

    result = await sdk.llm_function(instructions="hi", user_input="go")
    assert calls["n"] == 1
    assert "plain reply" in result.final_output


# ---------------------------------------------------------------------------
# CliHelperSDK._run_claude_oneshot subprocess bounds (Fix B)
# ---------------------------------------------------------------------------

class _CapturedOptions:
    """Stand-in for ClaudeAgentOptions that records the kwargs it was built
    with, so the test can assert on the subprocess env."""
    last: dict = {}

    def __init__(self, **kwargs):
        _CapturedOptions.last = kwargs


async def test_run_claude_oneshot_bounds_subprocess_env(monkeypatch):
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        cli_helper=CliHelperConfig(
            framework="claude_code", model="haiku",
            auth_type="api_key", api_key="sk-test",
        ),
    )
    monkeypatch.setattr(settings, "helper_cli_timeout_ms", 60000)
    monkeypatch.setattr(settings, "helper_cli_max_retries", 2)

    async def empty_query(prompt, options):
        if False:  # make this an async generator that yields nothing
            yield

    monkeypatch.setattr(cas, "ClaudeAgentOptions", _CapturedOptions)
    monkeypatch.setattr(cas, "query", empty_query)

    await CliHelperSDK()._run_claude_oneshot("system", "user", "haiku")

    env = _CapturedOptions.last["env"]
    # Helper-scoped bounds, NOT the agent-loop 600000 / 10.
    assert env["API_TIMEOUT_MS"] == "60000"
    assert env["CLAUDE_CODE_MAX_RETRIES"] == "2"


async def test_run_claude_oneshot_times_out(monkeypatch):
    set_user_config(
        ClaudeConfig(), OpenAIConfig(),
        cli_helper=CliHelperConfig(
            framework="claude_code", model="haiku",
            auth_type="api_key", api_key="sk-test",
        ),
    )
    monkeypatch.setattr(settings, "helper_cli_total_timeout_seconds", 0.3)

    async def slow_query(prompt, options):
        await asyncio.sleep(5)
        if False:
            yield

    monkeypatch.setattr(cas, "ClaudeAgentOptions", _CapturedOptions)
    monkeypatch.setattr(cas, "query", slow_query)

    with pytest.raises(TimeoutError):
        await CliHelperSDK()._run_claude_oneshot("system", "user", "haiku")
