"""
@file_name: test_output_transfer_user_text.py
@date: 2026-07-29
@description: A UserMessage carrying only TextBlocks must never surface as
assistant output.

Prod incident 2026-07-29 (PM Agent, agent_94360f6c4b98): when the Claude Code
CLI auto-compacts a long run it injects a **user-role** message holding the
compaction summary — which includes the absolute path of the CLI session
transcript and the trailing "Please continue the conversation from where we
left off". `_convert_user_to_stream_events` emitted that text as a text delta,
`response_processor` classified the delta as AGENT_RESPONSE, and
`run_collector` accumulated it into `events.final_output`. The owner then read
the CLI's internal bookkeeping — complete with a `/home/app/...jsonl` path they
cannot open — as if the agent had said it.

Assistant speech always arrives as `AssistantMessage`. `UserMessage` only ever
carries tool results and CLI-injected continuations, i.e. transcript plumbing.
The function's own comment already called this text "内部消息，不需要展示" but
only honoured that when a ToolResultBlock happened to be present.
"""

from __future__ import annotations

from xyz_agent_context.agent_framework.loop.events import DATA_TYPE_TEXT_DELTA
from xyz_agent_context.agent_framework.loop.output_transfer import output_transfer

# The literal trailer the Claude Code CLI appends to every auto-compaction
# hand-off, captured verbatim from prod evt_2d3fba0829fc4125.
_COMPACTION_TRAILER = (
    "Please continue the conversation from where we left off without asking "
    "the user any further questions. Continue with the last task that you "
    "were asked to work on."
)
_TRANSCRIPT_PATH = (
    "/home/app/.nexusagent/claude_config/projects/"
    "-opt-narranexus-workspaces-833832fe-agent-94360f6c4b98/"
    "6861ccf3-fa46-4097-ad2e-0e099e4a4b45.jsonl"
)


class TextBlock:
    def __init__(self, text: str) -> None:
        self.text = text


class ToolResultBlock:
    def __init__(self, content) -> None:
        self.content = content


class UserMessage:
    def __init__(self, content) -> None:
        self.content = content


class StreamEvent:
    """Assistant speech arrives token-by-token as StreamEvent, never as
    AssistantMessage text (which `_convert_assistant_to_stream_events`
    deliberately skips because the tokens already streamed)."""

    def __init__(self, text: str) -> None:
        self.event = {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": text},
        }


def _text_deltas(events) -> list[str]:
    """Every non-empty text delta the translator emitted."""
    out = []
    for e in events:
        data = e.get("data") or {}
        if data.get("type") == DATA_TYPE_TEXT_DELTA and data.get("delta"):
            out.append(data["delta"])
    return out


def test_user_message_with_only_text_emits_no_assistant_delta() -> None:
    msg = UserMessage([TextBlock("some CLI-injected plumbing")])
    events = output_transfer(msg, transfer_type="claude_agent_sdk", streaming=True)
    assert _text_deltas(events) == []


def test_compaction_summary_never_becomes_agent_output() -> None:
    """The exact prod payload must not reach the owner as agent speech."""
    summary = (
        "This session is being continued from a previous conversation that ran "
        "out of context. The conversation is summarized below:\n"
        "1. Primary Request and Intent: ...\n"
        "8. Current Work: attempting to update PM Notes...\n\n"
        f"If you need specific details from before compaction, read the full "
        f"transcript at: {_TRANSCRIPT_PATH}\n{_COMPACTION_TRAILER}"
    )
    events = output_transfer(
        UserMessage([TextBlock(summary)]),
        transfer_type="claude_agent_sdk",
        streaming=True,
    )
    joined = "".join(_text_deltas(events))
    assert _TRANSCRIPT_PATH not in joined
    assert "before compaction" not in joined
    assert _COMPACTION_TRAILER not in joined


def test_tool_result_blocks_are_still_emitted() -> None:
    """Removing the text passthrough must not touch tool-result plumbing."""
    msg = UserMessage([ToolResultBlock("tool said hi")])
    events = output_transfer(msg, transfer_type="claude_agent_sdk", streaming=True)
    outputs = [
        e["item"]["output"]
        for e in events
        if e.get("item", {}).get("type") == "tool_call_output_item"
    ]
    assert outputs == ["tool said hi"]


def test_mixed_text_and_tool_result_yields_only_the_tool_output() -> None:
    msg = UserMessage([TextBlock("plumbing"), ToolResultBlock("real result")])
    events = output_transfer(msg, transfer_type="claude_agent_sdk", streaming=True)
    assert _text_deltas(events) == []
    outputs = [
        e["item"]["output"]
        for e in events
        if e.get("item", {}).get("type") == "tool_call_output_item"
    ]
    assert outputs == ["real result"]


def test_assistant_speech_is_untouched() -> None:
    """The agent's own speech must still flow through as a text delta."""
    events = output_transfer(
        StreamEvent("here is your answer"),
        transfer_type="claude_agent_sdk",
        streaming=True,
    )
    assert "here is your answer" in "".join(_text_deltas(events))
