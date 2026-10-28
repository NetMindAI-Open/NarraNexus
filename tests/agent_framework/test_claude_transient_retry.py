"""
@file_name: test_claude_transient_retry.py
@date: 2026-09-03
@description: Same-session resume retry for a subscription account's transient
CLI error (rate_limit / server_error).

The Claude Code CLI never retries a 429 for a claude.ai subscription (its
retry predicate is literally ``status === 429 → !isSubscriber()``), so a
single "Opus is experiencing high load" ends the whole turn. The adapter now
retries THAT run on the same CLI session: the CLI's own transcript already
holds every tool call and result of the turn, so nothing is re-executed and
nothing streams twice. The gate is deliberately narrow — subscription auth,
the two transient enums, a known session id — and everything outside it must
behave exactly as before (the error event is passed through untouched).

Each test here goes red when the retry wrapper is removed: the assertions are
on the NUMBER of CLI runs, the ``resume`` handle the retry is spawned with,
and the presence/absence of the error event downstream.
"""
from __future__ import annotations

import asyncio

import pytest

import narranexus_plugins.frameworks_claude_code.sdk as sdk_mod
import narranexus_plugins.frameworks_claude_code.transcript as transcript_mod
from narranexus_plugins.frameworks_claude_code.sdk import (
    ClaudeAgentSDK,
    _inline_assistant_error_event,
)
from narranexus.platform.agent_framework.api_config import (
    ClaudeConfig,
    CodexConfig,
    OpenAIConfig,
    set_user_config,
)
from narranexus.contracts.agent_events import (
    DATA_TYPE_DONE,
    DATA_TYPE_DONE_SUPERSEDED_KEY,
    DATA_TYPE_ERROR,
    DATA_TYPE_RETRY,
    DATA_TYPE_USAGE,
)

from tests.agent_framework.test_claude_sdk_resume import ResultMessage, _StubClient

WORKING = "/tmp/ws-retry"
HIGH_LOAD = "Opus is experiencing high load, please use /model to switch to Sonnet"


class TextBlock:
    def __init__(self, text: str):
        self.text = text


class ToolUseBlock:
    def __init__(self, id: str, name: str, input: dict):
        self.id = id
        self.name = name
        self.input = input


class AssistantMessage:
    """Name matters: the adapter dispatches on type(message).__name__."""

    def __init__(self, content, error: str | None = None):
        self.content = content
        self.error = error


def _error_message(enum: str = "rate_limit", text: str = HIGH_LOAD) -> AssistantMessage:
    return AssistantMessage([TextBlock(text)], error=enum)


def _tool_call(tool_id: str = "toolu_1") -> AssistantMessage:
    return AssistantMessage([ToolUseBlock(tool_id, "mcp__x__y", {"a": 1})])


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    from narranexus.platform.settings import settings

    _StubClient.scripts = []
    _StubClient.instances = []
    monkeypatch.setattr(sdk_mod, "ClaudeSDKClient", _StubClient)
    # oauth_token = subscription auth WITHOUT the host-credential staging that
    # plain oauth performs before every spawn (irrelevant to this contract).
    set_user_config(
        claude=ClaudeConfig(api_key="sk-ant-oat-test", auth_type="oauth_token"),
        openai=OpenAIConfig(),
        codex=CodexConfig(),
    )
    monkeypatch.setattr(settings, "claude_synthetic_transcript_enabled", True)
    monkeypatch.setattr(settings, "claude_cli_config_path", str(tmp_path / "cfg"))
    monkeypatch.setattr(settings, "claude_transient_retry_attempts", 3)
    # No real waiting in tests; the schedule itself is covered separately.
    monkeypatch.setattr(settings, "claude_transient_retry_backoff_seconds", "0,0,0")
    monkeypatch.setattr(transcript_mod, "working_git_branch", lambda _p: "test-branch")
    yield


def _messages() -> list[dict]:
    return [
        {"role": "system", "content": "SYSTEM INSTRUCTIONS"},
        {"role": "user", "content": "old question", "_source": "chat"},
        {"role": "assistant", "content": "old answer", "_source": "chat"},
        {"role": "user", "content": "this turn input"},
    ]


def _messages_without_history() -> list[dict]:
    return [
        {"role": "system", "content": "SYSTEM INSTRUCTIONS"},
        {"role": "user", "content": "this turn input"},
    ]


async def _run(messages=None, **kwargs) -> list[dict]:
    sdk = ClaudeAgentSDK(working_path=WORKING)
    return [
        e async for e in sdk.agent_loop(messages or _messages(), {}, **kwargs)
    ]


def _data_types(events: list[dict]) -> list[str]:
    return [e.get("data", {}).get("type") for e in events if "data" in e]


def _errors(events: list[dict]) -> list[dict]:
    return [e["data"] for e in events if e.get("data", {}).get("type") == DATA_TYPE_ERROR]


# --- the retry itself -------------------------------------------------------


@pytest.mark.asyncio
async def test_subscription_rate_limit_resumes_the_same_session():
    _StubClient.scripts = [
        # First run: one tool call, then the CLI reports the 429 and ends.
        {"messages": [_tool_call(), _error_message(), ResultMessage("sess-A")]},
        # The retry continues and finishes cleanly.
        {"messages": [ResultMessage("sess-A")]},
    ]
    events = await _run()

    first, retry = _StubClient.instances
    assert len(_StubClient.instances) == 2
    # Same CLI session as the one the failed run reports in its completion
    # marker (the stub cannot echo our transcript id, so it reports its own;
    # the live CLI reports the id it was resumed with). The transcript already
    # holds the tool call above, so the retry never runs cold.
    assert first.options.resume, "a history-carrying turn resumes our transcript"
    assert retry.options.resume == "sess-A"
    # The retry is driven by a continuation nudge, not the user's message again.
    assert retry.queried != first.queried
    assert "retried" in retry.queried.lower() or "continue" in retry.queried.lower()
    # The user never sees the swallowed failure...
    assert _errors(events) == []
    # ...but does see that a retry is happening.
    retries = [e["data"] for e in events if e.get("data", {}).get("type") == DATA_TYPE_RETRY]
    assert len(retries) == 1
    assert retries[0]["attempt"] == 1 and retries[0]["max_attempts"] == 3
    assert retries[0]["error_type"] == "rate_limit"
    # Both completion markers reach downstream: the failed run's carries the
    # usage it really spent (accumulate_usage sums them), the retry's closes
    # the turn. Only the error event is swallowed.
    assert _data_types(events).count(DATA_TYPE_DONE) == 2


@pytest.mark.asyncio
async def test_server_error_is_retried_too():
    _StubClient.scripts = [
        {"messages": [_error_message("server_error", "API Error: 529 overloaded"), ResultMessage("s")]},
        {"messages": [ResultMessage("s")]},
    ]
    events = await _run()
    assert len(_StubClient.instances) == 2
    assert _errors(events) == []


@pytest.mark.asyncio
async def test_exhausted_attempts_surface_the_last_error_verbatim():
    _StubClient.scripts = [
        {"messages": [_error_message(), ResultMessage("s")]} for _ in range(4)
    ]
    events = await _run()

    assert len(_StubClient.instances) == 4, "1 run + 3 retries"
    errs = _errors(events)
    assert len(errs) == 1
    assert errs[0]["error_type"] == "rate_limit"
    # Point 1 of the request: the CLI's own text, as-is.
    assert errs[0]["error_message"].startswith(HIGH_LOAD)
    # The failed run's completion marker follows the error, as before.
    assert _data_types(events)[-1] == DATA_TYPE_DONE


@pytest.mark.asyncio
async def test_cold_first_turn_resumes_the_cli_reported_session():
    """No history → no self-authored transcript → the only session handle is
    the one the failed run's ResultMessage reports."""
    _StubClient.scripts = [
        {"messages": [_error_message(), ResultMessage("cli-made-id")]},
        {"messages": [ResultMessage("cli-made-id")]},
    ]
    events = await _run(_messages_without_history())

    first, retry = _StubClient.instances
    assert first.options.resume is None, "a genuine first turn starts cold"
    assert retry.options.resume == "cli-made-id"
    assert _errors(events) == []


@pytest.mark.asyncio
async def test_cold_first_turn_without_a_session_id_does_not_retry():
    _StubClient.scripts = [
        {"messages": [_error_message(), ResultMessage(session_id="")]},
        {"messages": [ResultMessage()]},
    ]
    events = await _run(_messages_without_history())
    assert len(_StubClient.instances) == 1
    assert len(_errors(events)) == 1


@pytest.mark.asyncio
async def test_failed_run_usage_is_still_billed():
    """accumulate_usage's sole source is response.done and it sums per run,
    so dropping the failed run's marker would silently under-bill every 429
    that lands after tool calls — exactly this feature's target case."""
    failed = ResultMessage("s")
    failed.usage = {"input_tokens": 100, "output_tokens": 7}
    _StubClient.scripts = [
        {"messages": [_tool_call(), _error_message(), failed]},
        {"messages": [ResultMessage("s")]},
    ]
    events = await _run()
    dones = [e["data"] for e in events if e.get("data", {}).get("type") == DATA_TYPE_DONE]
    assert [d["usage"]["input_tokens"] for d in dones] == [100, 10]
    # The replaced run's marker is flagged so the resume gate does not read
    # it as content; the live run's marker is not.
    assert [bool(d.get(DATA_TYPE_DONE_SUPERSEDED_KEY)) for d in dones] == [True, False]


class StreamEvent:
    """A streamed message_delta: output_transfer turns it into response.usage."""

    def __init__(self, output_tokens: int):
        self.event = {"type": "message_delta", "usage": {"output_tokens": output_tokens}}


@pytest.mark.asyncio
async def test_everything_but_the_error_is_released_on_retry():
    """The discard set is written as 'what is dropped' (the error itself), so
    a usage event between the error and the completion marker still reaches
    downstream — flagged superseded, like the marker."""
    _StubClient.scripts = [
        {"messages": [_error_message(), StreamEvent(42), ResultMessage("s")]},
        {"messages": [ResultMessage("s")]},
    ]
    events = await _run()
    usage = [e["data"] for e in events if e.get("data", {}).get("type") == DATA_TYPE_USAGE]
    assert len(usage) == 1 and usage[0]["usage"]["output_tokens"] == 42
    assert usage[0][DATA_TYPE_DONE_SUPERSEDED_KEY] is True
    assert _errors(events) == []


@pytest.mark.asyncio
async def test_retry_budget_and_numbering_span_the_whole_turn():
    """A turn can drive the wrapper twice (resume run, then the cold run after
    a refused resume). Attempts are numbered and bounded across both, so two
    `3.4.retry.1` rows can never appear in one turn."""
    _StubClient.scripts = [
        {"messages": [_error_message(), ResultMessage("s")]},  # resume run: 429
        {"messages": []},                                       # retry refused → cold
        {"messages": [_error_message(), ResultMessage("c")]},  # cold run: 429 again
        {"messages": [ResultMessage("c")]},                     # its retry succeeds
    ]
    events = await _run()
    retries = [e["data"]["attempt"] for e in events if e.get("data", {}).get("type") == DATA_TYPE_RETRY]
    assert retries == [1, 2]
    assert len(_StubClient.instances) == 4


@pytest.mark.asyncio
async def test_retry_prefers_the_session_the_cli_reports():
    """A CLI that forks the session on resume reports the NEW id in its
    completion marker; that file holds this run's tool results, so the retry
    must follow it rather than our original handle."""
    _StubClient.scripts = [
        {"messages": [_tool_call(), _error_message(), ResultMessage("forked-id")]},
        {"messages": [ResultMessage("forked-id")]},
    ]
    await _run()
    first, retry = _StubClient.instances
    assert first.options.resume != "forked-id"
    assert retry.options.resume == "forked-id"


@pytest.mark.asyncio
async def test_run_that_raises_after_the_error_releases_it_then_raises():
    """A CLI that exits non-zero right after reporting the 429 raises out of
    the run. The held error must be released first — otherwise the outer
    resume gate sees "no output" and cold-reruns the whole turn with the
    user's message, re-executing every tool."""
    _StubClient.scripts = [
        {"messages": [_error_message()], "raise_after": RuntimeError("CLI subprocess exited")},
        {"messages": [ResultMessage("s")]},
    ]
    with pytest.raises(RuntimeError, match="subprocess exited"):
        await _run()
    assert len(_StubClient.instances) == 1, "no cold re-run of the turn"


@pytest.mark.asyncio
async def test_retry_notice_does_not_disarm_the_resume_refused_cold_retry():
    """The resume-refused safety net (zero output before any content → cold
    re-run) keys on `yielded_any`. The retry notice is bookkeeping and must
    not arm it: 429 → retry run refused (zero output) → cold run completes,
    and the user never sees a no_output error."""
    _StubClient.scripts = [
        {"messages": [_error_message(), ResultMessage("s")]},
        {"messages": []},  # the resumed run is refused by the CLI
        {"messages": [ResultMessage("cold")]},
    ]
    events = await _run()
    assert len(_StubClient.instances) == 3
    assert _StubClient.instances[2].options.resume is None, "third run is the cold retry"
    assert _errors(events) == []


@pytest.mark.asyncio
async def test_retry_nudge_carries_the_reply_reminder():
    _StubClient.scripts = [
        {"messages": [_error_message(), ResultMessage("s")]},
        {"messages": [ResultMessage("s")]},
    ]
    await _run(expressive_tools=["mcp__chat_module__notify_owner"], origin_declaration="from chat")
    first, retry = _StubClient.instances
    assert "notify_owner" in first.queried
    assert "notify_owner" in retry.queried


@pytest.mark.asyncio
async def test_wait_before_retry_checks_cancellation_up_front():
    from narranexus_plugins.frameworks_claude_code.sdk import _wait_before_retry

    class _Flag:
        is_cancelled = True

        async def await_cancelled(self):
            await asyncio.Event().wait()  # never fires; only the up-front check can honour it

    assert await asyncio.wait_for(_wait_before_retry(30.0, _Flag()), timeout=1) is False


# --- the gate ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_key_accounts_are_not_retried():
    """The CLI already retries a 429 up to CLAUDE_CODE_MAX_RETRIES for keyed
    auth; a second layer would only spend the user's money."""
    set_user_config(
        claude=ClaudeConfig(api_key="k", auth_type="api_key"),
        openai=OpenAIConfig(),
        codex=CodexConfig(),
    )
    _StubClient.scripts = [
        {"messages": [_error_message(), ResultMessage("s")]},
        {"messages": [ResultMessage("s")]},
    ]
    events = await _run()
    assert len(_StubClient.instances) == 1
    assert len(_errors(events)) == 1


@pytest.mark.asyncio
async def test_non_transient_enums_are_not_retried():
    _StubClient.scripts = [
        {"messages": [_error_message("invalid_request", "API Error: 400 bad"), ResultMessage("s")]},
        {"messages": [ResultMessage("s")]},
    ]
    events = await _run()
    assert len(_StubClient.instances) == 1
    assert _errors(events)[0]["error_type"] == "invalid_request"


@pytest.mark.asyncio
async def test_disabled_by_setting(monkeypatch):
    from narranexus.platform.settings import settings

    monkeypatch.setattr(settings, "claude_transient_retry_attempts", 0)
    _StubClient.scripts = [
        {"messages": [_error_message(), ResultMessage("s")]},
        {"messages": [ResultMessage("s")]},
    ]
    events = await _run()
    assert len(_StubClient.instances) == 1
    assert len(_errors(events)) == 1


@pytest.mark.asyncio
async def test_cli_that_keeps_going_after_the_error_is_left_alone():
    """An inline error followed by real output means the CLI recovered on its
    own. The held-back events must be released in their original order and no
    retry may be spawned — otherwise the continuation would double the work."""
    _StubClient.scripts = [
        {"messages": [_error_message(), _tool_call("toolu_after"), ResultMessage("s")]},
        {"messages": [ResultMessage("s")]},
    ]
    events = await _run()

    assert len(_StubClient.instances) == 1
    types = _data_types(events)
    assert DATA_TYPE_ERROR in types
    tool_ids = [
        e["item"]["tool_call_id"] for e in events if e.get("type") == "run_item_stream_event"
    ]
    assert tool_ids == ["toolu_after"]
    # Error first, then the tool call, then the completion marker.
    assert types.index(DATA_TYPE_ERROR) < len(types) - 1
    assert types[-1] == DATA_TYPE_DONE


@pytest.mark.asyncio
async def test_cancellation_during_backoff_surfaces_the_error_without_a_retry(monkeypatch):
    from narranexus.platform.settings import settings

    monkeypatch.setattr(settings, "claude_transient_retry_backoff_seconds", "30")

    class _Token:
        def __init__(self):
            self._ev = asyncio.Event()

        @property
        def is_cancelled(self) -> bool:
            return self._ev.is_set()

        async def await_cancelled(self):
            await self._ev.wait()

        def fire(self):
            self._ev.set()

    token = _Token()
    _StubClient.scripts = [
        {"messages": [_error_message(), ResultMessage("s")]},
        {"messages": [ResultMessage("s")]},
    ]

    async def _cancel_soon():
        await asyncio.sleep(0.05)
        token.fire()

    asyncio.get_running_loop().create_task(_cancel_soon())
    events = await asyncio.wait_for(_run(cancellation=token), timeout=5)

    assert len(_StubClient.instances) == 1, "no retry may start after cancel"
    assert len(_errors(events)) == 1


# --- the schedule -----------------------------------------------------------


def test_backoff_schedule_pads_with_its_last_value():
    from narranexus_plugins.frameworks_claude_code.sdk import _retry_delay_seconds

    assert _retry_delay_seconds("15,30,60", 1) == 15
    assert _retry_delay_seconds("15,30,60", 3) == 60
    assert _retry_delay_seconds("15,30,60", 7) == 60
    assert _retry_delay_seconds("garbage", 2) == 0
    assert _retry_delay_seconds("", 1) == 0


# --- point 1: the CLI's text, as-is -----------------------------------------


def test_assistant_text_is_the_message_verbatim():
    d = _inline_assistant_error_event("rate_limit", None, assistant_text=HIGH_LOAD)["data"]
    assert d["error_message"].startswith(HIGH_LOAD)
    assert d["error_type"] == "rate_limit"


def test_assistant_text_and_stderr_both_survive():
    d = _inline_assistant_error_event(
        "unknown", ["litellm detail 75307 tokens"], assistant_text="API Error: 400 x"
    )["data"]
    assert d["error_message"].startswith("API Error: 400 x")
    assert "75307" in d["error_message"]
