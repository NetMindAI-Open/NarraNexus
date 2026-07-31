"""
@file_name: test_claude_synthetic_transcript.py
@date: 2026-07-29
@description: Adapter-level contract for the self-authored resume transcript.

The builder itself is covered by test_claude_transcript.py (determinism, uuid
chain, leaf pointer) and its IO by test_claude_transcript_io.py (fail-open write,
tolerant remove). What is pinned HERE is the part only the adapter can get
wrong: writing the file before the spawn, pointing ``options.resume`` at it,
keeping history OUT of the prompt, and deleting the file on every exit path.

Why each matters:

* **History out of the prompt** is the entire point. The cache matches a strict
  byte prefix ordered tools → system → messages; history in the system prompt
  sits inside it, so every turn voids the prefix. Measured: with history moved
  out and the prompt byte-stable, a second consecutive resume round's full-price
  input fell from 49,137 to 2,247.
* **A fresh session id per turn** is what allows deleting the file, which is
  what removes the cross-tenant read risk (a lingering transcript in the shared
  CLAUDE_CONFIG_DIR plus a guessable handle, on an intentionally
  unauthenticated /agent-loop). A derived, stable id would reopen it.
* **Cleanup on every exit** — success, exception, cancellation — because a fresh
  id per turn means nothing else will ever come back and remove a stranded file.
* **Fail-open** because the transcript is an optimization and 铁律 #14 forbids
  letting one break an agent run.
"""
from __future__ import annotations

import asyncio

import pytest

import xyz_agent_context.agent_framework.adapters.claude.sdk as sdk_mod
import xyz_agent_context.agent_framework.adapters.claude.transcript as transcript_mod
from xyz_agent_context.agent_framework.adapters.claude.sdk import ClaudeAgentSDK
from xyz_agent_context.agent_framework.api_config import (
    ClaudeConfig,
    CodexConfig,
    OpenAIConfig,
    set_user_config,
)

from tests.agent_framework.test_claude_sdk_resume import ResultMessage, _StubClient

HISTORY_MARK = "=== Chat History ==="
WORKING = "/tmp/ws-synth"


@pytest.fixture(autouse=True)
def _env(monkeypatch, tmp_path):
    from xyz_agent_context.settings import settings

    _StubClient.scripts = []
    _StubClient.instances = []
    monkeypatch.setattr(sdk_mod, "ClaudeSDKClient", _StubClient)
    set_user_config(
        claude=ClaudeConfig(api_key="k", auth_type="api_key"),
        openai=OpenAIConfig(),
        codex=CodexConfig(),
    )
    monkeypatch.setattr(settings, "claude_synthetic_transcript_enabled", True)
    # api_key auth resolves CLAUDE_CONFIG_DIR to claude_cli_config_path, so this
    # is where the adapter will place the transcript.
    monkeypatch.setattr(settings, "claude_cli_config_path", str(tmp_path / "cfg"))
    # Keep the turn off the real git binary: the branch field is cosmetic and a
    # subprocess per test is noise. Patched where `prepare_transcript` looks it
    # up (the transcript module) — it moved there from the adapter so the git
    # lookup lives next to its only consumer.
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


def _transcripts(tmp_path):
    return sorted((tmp_path / "cfg" / "projects").rglob("*.jsonl"))


# --- the happy path ---------------------------------------------------------


@pytest.mark.asyncio
async def test_history_leaves_the_prompt_and_resume_is_set(tmp_path):
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    await _run()

    options = _StubClient.instances[0].options
    assert options.resume, "a synthetic transcript must be resumed"
    assert HISTORY_MARK not in options.system_prompt, (
        "history must ride the transcript, not the cache prefix"
    )
    # System instructions still go every round — only history moved.
    assert "SYSTEM INSTRUCTIONS" in options.system_prompt


@pytest.mark.asyncio
async def test_the_session_id_is_fresh_each_turn(tmp_path):
    """Stable ids would be guessable, which is what the delete-after-use
    lifecycle exists to avoid."""
    _StubClient.scripts = [{"messages": [ResultMessage()]}, {"messages": [ResultMessage()]}]
    await _run()
    await _run()
    first, second = (i.options.resume for i in _StubClient.instances[:2])
    assert first and second and first != second


@pytest.mark.asyncio
async def test_transcript_is_deleted_after_a_successful_turn(tmp_path):
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    await _run()
    assert _transcripts(tmp_path) == [], "a lingering transcript is a read path"


@pytest.mark.asyncio
async def test_the_file_exists_while_the_cli_runs(tmp_path):
    """Written BEFORE the spawn — a transcript that appears afterwards would be
    resumed by nothing."""
    seen: list[int] = []

    real_connect = _StubClient.connect

    async def _spy(self):
        seen.append(len(_transcripts(tmp_path)))
        await real_connect(self)

    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    try:
        _StubClient.connect = _spy
        await _run()
    finally:
        _StubClient.connect = real_connect
    assert seen == [1]


# --- cleanup on the unhappy paths ------------------------------------------


@pytest.mark.asyncio
async def test_transcript_is_deleted_when_the_run_raises(tmp_path):
    """The failure has to come AFTER output, because a zero-output failure on a
    self-authored transcript is retried cold rather than propagated (see
    test_any_resume_failure_falls_back_...). Once content has been yielded the
    exception is real, and the finally still has to clean up."""
    _StubClient.scripts = [
        {"messages": [ResultMessage()], "raise_after": RuntimeError("boom")}
    ]
    with pytest.raises(RuntimeError):
        await _run()
    assert _transcripts(tmp_path) == []


@pytest.mark.asyncio
async def test_transcript_is_deleted_when_the_generator_is_abandoned(tmp_path):
    """Cancellation closes the generator mid-flight; the finally must run then
    too, which is why removal is synchronous."""
    _StubClient.scripts = [
        {"messages": [ResultMessage(), ResultMessage(), ResultMessage()]}
    ]
    sdk = ClaudeAgentSDK(working_path=WORKING)
    gen = sdk.agent_loop(_messages(), {})
    await gen.__anext__()
    assert len(_transcripts(tmp_path)) == 1
    await gen.aclose()
    assert _transcripts(tmp_path) == []


# --- fail-open --------------------------------------------------------------


@pytest.mark.asyncio
async def test_unwritable_config_dir_falls_back_to_history_in_prompt(
    tmp_path, monkeypatch
):
    from xyz_agent_context.settings import settings

    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    monkeypatch.setattr(settings, "claude_cli_config_path", str(ro))
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    try:
        await _run()
    finally:
        ro.chmod(0o700)

    options = _StubClient.instances[0].options
    assert options.resume is None, "no transcript → nothing to resume"
    assert HISTORY_MARK in options.system_prompt, "history must come back"


@pytest.mark.asyncio
async def test_no_history_runs_a_genuine_first_turn(tmp_path):
    """Nothing to resume: no file, no resume, and today's cold behavior."""
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    await _run(messages=_messages_without_history())

    options = _StubClient.instances[0].options
    assert options.resume is None
    assert _transcripts(tmp_path) == []


@pytest.mark.asyncio
async def test_gate_off_restores_the_previous_behavior(tmp_path, monkeypatch):
    from xyz_agent_context.settings import settings

    monkeypatch.setattr(settings, "claude_synthetic_transcript_enabled", False)
    _StubClient.scripts = [{"messages": [ResultMessage()]}]
    await _run()

    options = _StubClient.instances[0].options
    assert options.resume is None
    assert HISTORY_MARK in options.system_prompt
    assert _transcripts(tmp_path) == []


@pytest.mark.asyncio
async def test_any_resume_failure_falls_back_when_we_authored_the_transcript(tmp_path):
    """A transcript WE wrote is fresh and valid by construction, so if the CLI
    refuses to resume it, the cause is our bug — and a cold retry is always the
    right answer, whatever the CLI says.

    This is not hypothetical. The first live run wrote the file into the wrong
    directory (the cwd slug did not translate dots and underscores), and the
    turn only survived because the CLI happened to answer "No conversation
    found" — the exact phrase the handle-based stale check greps for. Any other
    rejection (a malformed record, a format change after a CLI upgrade) would
    have failed the turn outright and shown the user an error, which 铁律 #14
    and #16 both forbid. So the fallback must not depend on matching a string
    written for a different failure.
    """
    _StubClient.scripts = [
        # Zero output, then a failure whose text says nothing about sessions.
        {"messages": [], "stderr": ["something else entirely"],
         "raise_after": RuntimeError("exit code 1")},
        # The cold retry.
        {"messages": [ResultMessage()]},
    ]
    events = await _run()

    assert len(_StubClient.instances) == 2, "expected exactly one cold retry"
    first, retry = _StubClient.instances
    assert first.options.resume, "first attempt resumed our transcript"
    assert retry.options.resume is None, "the retry must run cold"
    assert HISTORY_MARK in retry.options.system_prompt, "history returns on the retry"
    # The user must not perceive the miss (铁律 #16): no error event surfaced.
    assert not any(
        e.get("data", {}).get("type") == "response.error" for e in events
    )
    assert _transcripts(tmp_path) == []


@pytest.mark.asyncio
async def test_a_credential_failure_is_not_retried(tmp_path):
    """The one pre-output failure that must NOT be retried.

    A dead credential also dies before any output, so the type-blind rule above
    would retry it — and that retry is guaranteed to fail identically, costing a
    second CLI spawn and doubling the time before the user sees the real error.
    The retry exists to cover OUR transcript bugs; a credential failure is not
    one, and retrying does not make it one.
    """
    _StubClient.scripts = [
        {"messages": [], "raise_after": RuntimeError("401 unauthorized")},
        # A second script is provided on purpose: if the code wrongly retried,
        # this would silently absorb it and the test would pass for the wrong
        # reason. The instance-count assertion is what actually catches it.
        {"messages": [ResultMessage()]},
    ]
    with pytest.raises(RuntimeError, match="401"):
        await _run()

    assert len(_StubClient.instances) == 1, "a credential failure must not retry"
    assert _transcripts(tmp_path) == [], "cleanup still runs on the raising path"


@pytest.mark.asyncio
async def test_a_failure_after_output_still_raises(tmp_path):
    """The retry is bounded to the pre-output window. Once content has been
    yielded, re-running would duplicate it — this stays a startup fallback, not
    a retry loop (铁律 #14)."""
    _StubClient.scripts = [
        {"messages": [ResultMessage()], "raise_after": RuntimeError("mid-stream")},
    ]
    with pytest.raises(RuntimeError):
        await _run()
    assert len(_StubClient.instances) == 1
    assert _transcripts(tmp_path) == []




# --- keep the async fixtures honest ----------------------------------------


def test_event_loop_is_not_left_running():
    """Guard against a test above leaking a loop and masking failures later."""
    with pytest.raises(RuntimeError):
        asyncio.get_running_loop()
