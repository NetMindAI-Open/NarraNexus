"""
@file_name: test_claude_sdk_resume.py
@author:
@date: 2026-07-28
@description: Claude adapter CLI teardown, with the SDK transport stubbed out.

Scope narrowed 2026-07-29. This file used to also cover handle-based resume (an
upstream-supplied session id, the validation guarding it, and a retry keyed on
the CLI's "No conversation found" phrase). The adapter now authors the CLI
transcript itself every turn, so none of that exists — see
test_claude_synthetic_transcript.py. What is left is teardown, which does not
care where the session id came from:

* natural completion → graceful CLI shutdown (stdin close + bounded wait)
  BEFORE disconnect's SIGTERM teardown; cancellation skips it. That clean exit
  is what flushes the transcript the next turn replays — the 2026-07-25
  regression was exactly this ordering;
* teardown liveness (2026-07-28): a HANGING ``end_input()`` is bounded on its
  own (suppress() does nothing for a hang) and a cancellation raised DURING
  the graceful wait short-circuits to the fast teardown instead of buying the
  user the full graceful ceiling;
* a failure AFTER content re-raises — the same-turn retry stays bounded to the
  pre-output window.

The synthetic-transcript gate is switched OFF here so these cases exercise the
plain single-run path and stay about teardown alone.
"""
from __future__ import annotations

import asyncio
import time

import pytest

import xyz_agent_context.agent_framework.adapters.claude.sdk as sdk_mod
from xyz_agent_context.agent_framework.adapters.claude.sdk import ClaudeAgentSDK
from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    CodexConfig,
    OpenAIConfig,
    set_user_config,
)

HISTORY_MARK = "=== Chat History ==="


class ResultMessage:
    """Name matters: output_transfer dispatches on type(message).__name__."""

    def __init__(self, session_id: str = "cli_new_session"):
        self.usage = {"input_tokens": 10, "output_tokens": 5}
        self.total_cost_usd = 0.0
        self.num_turns = 1
        self.session_id = session_id
        self.stop_reason = "end_turn"


class _StubProcess:
    """Mimics the transport's subprocess handle used by the graceful path.

    ``process_hangs`` in the script simulates a CLI that never exits after
    stdin close — the case the graceful wait's bound (and its cancellation
    race) exists for.
    """

    def __init__(self, owner: "_StubClient"):
        self._owner = owner
        self.returncode = None

    async def wait(self):
        self._owner.calls.append("process_wait")
        if self._owner._script.get("process_hangs"):
            await asyncio.Event().wait()  # never resolves
        self.returncode = 0
        return 0


class _StubTransport:
    """Mimics the SDK transport surface `_graceful_cli_shutdown` reaches into.

    Script hooks:
      ``end_input_hangs``   — end_input() never returns (a stuck concurrent
                              write holding transport._write_lock).
      ``cancel_on_end_input`` — a token whose event is set from inside
                              end_input(), i.e. Stop pressed just AFTER the
                              caller's cancellation gate.
    """

    def __init__(self, owner: "_StubClient"):
        self._owner = owner
        self._process = _StubProcess(owner)

    async def end_input(self):
        self._owner.calls.append("end_input")
        token = self._owner._script.get("cancel_on_end_input")
        if token is not None:
            token.fire()
        if self._owner._script.get("end_input_hangs"):
            await asyncio.Event().wait()  # never resolves


class _StubClient:
    """Stub for ClaudeSDKClient: replays a per-instance script.

    ``scripts`` is a list of dicts consumed in construction order:
      {"messages": [...], "stderr": [...], "raise_after": Exception | None}

    Records lifecycle calls in ``calls`` (connect / query / end_input /
    process_wait / disconnect) so tests can assert TEARDOWN ORDER — the
    2026-07-25 transcript-flush regression was exactly an ordering bug
    (SIGTERM before the CLI's own clean exit).
    """

    scripts: list[dict] = []
    instances: list["_StubClient"] = []

    def __init__(self, options):
        self.options = options
        self.queried: str | None = None
        self.calls: list[str] = []
        self._transport = _StubTransport(self)
        idx = len(type(self).instances)
        self._script = type(self).scripts[idx] if idx < len(type(self).scripts) else {}
        type(self).instances.append(self)

    async def connect(self):
        self.calls.append("connect")
        # Feed the scripted CLI stderr through the real callback so the
        # adapter's stale-handle detection reads it exactly like production.
        for line in self._script.get("stderr", []):
            self.options.stderr(line)

    async def query(self, message):
        self.calls.append("query")
        self.queried = message

    def receive_response(self):
        messages = self._script.get("messages", [])
        raise_after = self._script.get("raise_after")

        async def _gen():
            for m in messages:
                yield m
            if raise_after is not None:
                raise raise_after

        return _gen()

    async def disconnect(self):
        self.calls.append("disconnect")


@pytest.fixture(autouse=True)
def _stub_transport(monkeypatch):
    _StubClient.scripts = []
    _StubClient.instances = []
    monkeypatch.setattr(sdk_mod, "ClaudeSDKClient", _StubClient)
    # Deterministic provider config for the ambient ContextVar proxy.
    set_user_config(
        claude=ClaudeConfig(api_key="k", auth_type="api_key"),
        openai=OpenAIConfig(),
        codex=CodexConfig(),
    )
    # This file covers the HANDLE-based resume path: a session id supplied by
    # upstream, which may turn out stale. The self-authored transcript
    # (2026-07-29) is a different mechanism that makes every turn a resume turn
    # and cannot go stale, so it is switched off here — otherwise the
    # "no kwarg → cold start" cases below would silently become resume cases and
    # stop testing what they name. Its own coverage lives in
    # test_claude_synthetic_transcript.py.
    from xyz_agent_context.settings import settings

    monkeypatch.setattr(settings, "claude_synthetic_transcript_enabled", False)
    yield


def _messages() -> list[dict]:
    return [
        {"role": "system", "content": "SYSTEM INSTRUCTIONS"},
        {"role": "user", "content": "old question", "_source": "chat"},
        {"role": "assistant", "content": "old answer", "_source": "chat"},
        {"role": "user", "content": "this turn input"},
    ]


async def _run(**kwargs) -> list[dict]:
    sdk = ClaudeAgentSDK(working_path="/tmp/ws")
    return [e async for e in sdk.agent_loop(_messages(), {}, **kwargs)]


# ---------------------------------------------------------------------------
# NOTE (2026-07-29): the R2/R3 handle cases used to live here — an upstream-
# supplied session id, the four-fold validation that guarded it, and a retry
# that fired only when the CLI stderr carried the exact phrase
# "No conversation found". None of that exists anymore: the adapter authors
# the transcript itself every turn, so there is no handle to supply, nothing
# that can go stale, and the retry now fires on ANY refusal before output.
# Coverage moved to test_claude_synthetic_transcript.py. What remains below is
# CLI teardown, which is independent of where the session id came from.
# ---------------------------------------------------------------------------

# ---------------------------------------------------------------------------
# 2026-07-25 transcript-flush regression: teardown ORDER on normal completion
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_normal_completion_graceful_shutdown_before_teardown():
    # The CLI must get its own clean exit (stdin close → process exits)
    # BEFORE disconnect()'s SIGTERM teardown — that clean exit is what
    # flushes the session transcript `--resume` replays next turn.
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    await _run()

    (client,) = _StubClient.instances
    assert client.calls == ["connect", "query", "end_input", "process_wait", "disconnect"]
    # After the graceful wait the process is already gone — the transport's
    # close() (inside disconnect) then skips SIGTERM entirely.
    assert client._transport._process.returncode == 0


@pytest.mark.asyncio
async def test_resume_run_also_gets_graceful_shutdown():
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    await _run()

    (client,) = _StubClient.instances
    assert client.calls == ["connect", "query", "end_input", "process_wait", "disconnect"]


class _CancelledToken:
    is_cancelled = True

    async def await_cancelled(self):
        return None


@pytest.mark.asyncio
async def test_cancelled_run_skips_graceful_shutdown_but_still_tears_down():
    # Cancellation keeps the fast synchronous teardown: NO graceful wait
    # (the user pressed stop — do not linger), straight to disconnect.
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    sdk = ClaudeAgentSDK(working_path="/tmp/ws")
    _ = [
        e
        async for e in sdk.agent_loop(_messages(), {}, cancellation=_CancelledToken())
    ]

    (client,) = _StubClient.instances
    assert "end_input" not in client.calls
    assert "process_wait" not in client.calls
    assert client.calls[-1] == "disconnect"


# ---------------------------------------------------------------------------
# 2026-07-28 teardown liveness: bounded end_input + cancel-during-graceful
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_hanging_end_input_is_bounded_and_turn_still_completes(monkeypatch):
    # The vendored SDK's end_input() takes transport._write_lock; a stuck
    # concurrent write makes it hang forever, and `suppress(Exception)` does
    # NOTHING for a hang. It must have its own bound and then fall through to
    # the SIGTERM/SIGKILL teardown path.
    monkeypatch.setattr(sdk_mod, "_GRACEFUL_END_INPUT_SECONDS", 0.05)
    monkeypatch.setattr(sdk_mod, "_GRACEFUL_CLI_EXIT_SECONDS", 0.05)
    _StubClient.scripts = [
        {
            "messages": [ResultMessage()],
            "end_input_hangs": True,
            "process_hangs": True,  # nothing rescues us but the bound
        }
    ]
    started = time.monotonic()
    events = await _run()
    elapsed = time.monotonic() - started

    # The turn completed normally — the hang never reached the user.
    types = [e.get("data", {}).get("type") for e in events]
    assert "response.done" in types
    # Bounded by the two small ceilings, nowhere near the 10s default.
    assert elapsed < 2.0
    (client,) = _StubClient.instances
    assert client.calls[-1] == "disconnect"  # fell through to the kill path


class _LateCancelToken:
    """Not cancelled at the caller's gate; fires DURING graceful shutdown."""

    def __init__(self):
        self._event = asyncio.Event()

    @property
    def is_cancelled(self) -> bool:
        return self._event.is_set()

    def fire(self) -> None:
        self._event.set()

    async def await_cancelled(self):
        await self._event.wait()


@pytest.mark.asyncio
async def test_cancel_during_graceful_wait_short_circuits_to_teardown():
    # Stop pressed a millisecond AFTER the caller's one-shot gate: the
    # graceful wait must lose the race to the token instead of holding the
    # user for the full _GRACEFUL_CLI_EXIT_SECONDS.
    token = _LateCancelToken()
    _StubClient.scripts = [
        {
            "messages": [ResultMessage()],
            "cancel_on_end_input": token,   # fires once we're already inside
            "process_hangs": True,          # CLI would never exit on its own
        }
    ]
    sdk = ClaudeAgentSDK(working_path="/tmp/ws")
    started = time.monotonic()
    _ = [e async for e in sdk.agent_loop(_messages(), {}, cancellation=token)]
    elapsed = time.monotonic() - started

    # Fast teardown — NOT the 10s graceful ceiling.
    assert elapsed < 2.0
    assert sdk_mod._GRACEFUL_CLI_EXIT_SECONDS == 10.0  # ceiling itself unchanged
    (client,) = _StubClient.instances
    # We did enter the graceful path (gate passed), then bailed out on cancel.
    assert "end_input" in client.calls
    assert client.calls[-1] == "disconnect"
    assert client._transport._process.returncode is None  # no clean exit waited


@pytest.mark.asyncio
async def test_normal_completion_still_waits_the_full_graceful_exit():
    # Guard against "fix cancellation, regress the transcript flush": with NO
    # cancellation the graceful wait must still await the CLI's own exit.
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    await _run()
    (client,) = _StubClient.instances
    assert client.calls == ["connect", "query", "end_input", "process_wait", "disconnect"]
    assert client._transport._process.returncode == 0


@pytest.mark.asyncio
async def test_failure_after_content_reraises_without_retry():
    # Content already reached the user → NOT "failed-before-any-content";
    # the crash must flow through the existing error path, no retry.
    _StubClient.scripts = [
        {
            "messages": [ResultMessage()],
            "stderr": ["No conversation found"],
            "raise_after": RuntimeError("boom mid-stream"),
        },
        {"messages": [ResultMessage()]},  # must NOT be consumed
    ]
    with pytest.raises(RuntimeError, match="boom mid-stream"):
        await _run()
    assert len(_StubClient.instances) == 1
