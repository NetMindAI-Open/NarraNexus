"""
@file_name: run_collector.py
@author: Bin Liang
@date: 2026-04-20
@description: Shared collection helper for consumers of AgentRuntime.run().

Historically each trigger (LarkTrigger, JobTrigger, MessageBusTrigger,
ChatTrigger A2A) wrote its own ``async for msg in runtime.run(...)``
loop. That caused two bugs:

  1. Each loop only handled ``MessageType.AGENT_RESPONSE`` — ``ERROR``
     messages were silently dropped (Bug 2 surface symptom on Lark).
  2. Each loop re-implemented the same "accumulate deltas / track
     tool calls / capture raw payloads" logic slightly differently.

This module provides a single ``collect_run`` helper that reads every
message type once and returns a structured ``RunCollection``. Each
trigger only has to implement its own policy for displaying / logging
the error when ``result.is_error`` is true. A new trigger (Telegram,
Slack, Discord, ...) can adopt the same pattern with zero risk of
re-introducing the silent-drop bug.

Used by:
  - module/lark_module/lark_trigger.py (LarkTrigger._build_and_run_agent)
  - module/job_module/job_trigger.py (JobTrigger)
  - message_bus/message_bus_trigger.py (MessageBusTrigger)
  - module/chat_module/chat_trigger.py (ChatTrigger A2A handler)

Not used by the WebSocket route (``backend/routes/websocket.py``): that
path streams messages to the frontend live instead of collecting them,
and the frontend already knows how to render every message type.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional

from loguru import logger

from narranexus.contracts.agent_events import ITEM_TYPE_TOOL_CALL
from narranexus.platform.schema.runtime_message import MessageType


#: Severities that are a VERDICT on a fatal rather than a competing claim about
#: it: ``recovered`` (the helper-LLM fallback answered anyway) and
#: ``recovered_after_reply`` (the agent had already spoken). Both are only ever
#: emitted BECAUSE a fatal happened, so any rule that reasons "a fatal was seen,
#: therefore fatal wins" has to exempt them or it overrules the only thing they
#: exist to say.
#:
#: One name because this knowledge had drifted into three copies in this file,
#: and the drift's symptom is concrete: add a fourth "answered anyway" severity
#: to one list and not the other, and a turn that produced a real reply gets a
#: failure notice in its place. That bug has already been paid for once here.
VERDICT_ON_FATAL_SEVERITIES = ("recovered", "recovered_after_reply")


@dataclass(frozen=True)
class RunError:
    """A failure surfaced by AgentRuntime via a ``MessageType.ERROR`` event.

    Attributes:
        error_type: Concrete class name of the underlying exception,
            preserved by AgentRuntime so consumers can branch on it
            (e.g. show a friendlier text for
            ``LLMConfigNotConfigured`` than for a generic CLI crash).
        error_message: Human-readable explanation. May be surfaced to
            the owner in web chat verbatim, or replaced by a friendlier
            text for IM channels where the sender is not the owner.
        severity: What the failure means for the turn's OUTPUT. FOUR values,
            and a consumer that knows only two will discard a correct answer
            or announce a failure that did not happen:
            ``"fatal"`` — the turn has no usable output.
            ``"recoverable"`` — a transient provider hiccup the loop absorbed;
            it kept going and produced a real reply.
            ``"recovered"`` — a fatal-class failure the helper-LLM fallback
            papered over with a real reply.
            ``"recovered_after_reply"`` — the agent had already spoken when
            the failure landed.
            The last two are a VERDICT on a fatal rather than a competing
            claim about the turn (see ``VERDICT_ON_FATAL_SEVERITIES``), which
            is why the sticky-fatal rule at collection time exempts them.
            Empty when the runtime did not say — read as fatal, because
            calling a possibly-empty turn a success is the worse mistake.
            Not reachable from ``runtime.run()`` today (``ErrorMessage``
            declares the four as a ``Literal`` and defaults to ``"fatal"``):
            an empty value means a hand-built ``RunError``, so treating it
            defensively costs nothing and assumes nothing.
    """

    error_type: str
    error_message: str
    severity: str = ""


def _add_segment(segments: list[dict], kind: str, text: str) -> None:
    """Append text, merging into the previous segment when the kind matches.

    Deltas arrive in fragments, so without merging one thought would render as
    six bubbles — the rhythm this exists to show would be noise instead.

    Accumulates into a list of PARTS, joined once at the end, exactly like
    ``text_parts`` beside it. The first version did ``segments[-1]["text"] +=
    text``, and CPython's in-place concatenation optimisation cannot fire on a
    string the dict still references — so every delta copied the whole segment
    so far, making a long reply quadratic in its own length. This path runs on
    EVERY agent turn, and iron rule #14 makes runs of tens of thousands of
    deltas a first-class case, not an outlier.
    """
    if segments and segments[-1]["kind"] == kind:
        segments[-1]["parts"].append(text)
    else:
        segments.append({"kind": kind, "parts": [text]})


def joined_segments(segments: list[dict]) -> list[dict]:
    """Turn the accumulator's `{kind, parts}` into the contract's `{kind, text}`.

    Exported because the team-room deliverer reads the segments MID-RUN: since
    the room post moved inside the turn, it needs the boundary for the text it
    is posting, and the collection does not exist yet. The join is done in one
    place so an in-flight reader and the final return cannot disagree about the
    shape — and the parts form never escapes either way.
    """
    return [{"kind": s["kind"], "text": "".join(s["parts"])} for s in segments]


@dataclass
class RunCollection:
    """Result of consuming one ``AgentRuntime.run()`` invocation."""

    output_text: str = ""
    """What the agent said, in arrival order: every ``AGENT_RESPONSE.delta``,
    plus — only when the caller opted in via ``include_monologue`` — every
    ``AGENT_THINKING.monologue`` segment (NexusPower's assistant plain text,
    which streams as thinking under the monologue contract)."""

    segments: list[dict] = field(default_factory=list)
    """``output_text`` with the monologue/reply boundary still intact:
    ``[{"kind": "monologue"|"reply", "text": str}]`` in arrival order,
    consecutive pieces of one kind merged.

    Exists because that boundary is destroyed by the join above and cannot be
    recovered downstream. A team room wants to lay deliberation out differently
    from an answer, and the private chat's `segmentTurn` cannot help: it cuts a
    turn from the EVENT STREAM, which no longer exists by the time a room
    message does. A frontend heuristic could only guess, and guessing wrong
    renders thinking as conclusion or the reverse.

    Present on every run, not only ``include_monologue`` ones: a turn with no
    monologue is simply one ``reply`` segment. Only the MONOLOGUE segments
    depend on the opt-in — so a non-empty ``segments`` says nothing about
    whether this was a team turn, and code that needs to know must ask.

    Empty for a silent or whitespace-only turn, so a caller cannot render a
    blank bubble from it.
    ``"".join(s["text"] for s in segments) == output_text`` always holds."""

    tool_calls: list[str] = field(default_factory=list)
    """Names of tools invoked by the agent, in arrival order."""

    raw_items: list[Any] = field(default_factory=list)
    """The ``.raw`` payload of every message that had one. LarkTrigger
    uses this to extract the exact text the agent sent via
    ``lark_cli im +messages-send``."""

    error: Optional[RunError] = None
    """``None`` when the run succeeded; a ``RunError`` when AgentRuntime
    yielded one or more ``ERROR`` messages. If multiple errors arrived
    the last one wins (callers get the most specific failure)."""

    event_id: Optional[str] = None
    """``events`` row id of this turn, captured from the Step-0 progress
    message (``details.event_id``). None if the run died before Step 0
    completed. Lets bus consumers link the turn to its persisted
    event_log without touching the runtime."""

    @property
    def is_error(self) -> bool:
        """Any failure frame reached the collector, recoverable ones included.

        Consumers deciding whether the turn produced usable OUTPUT want
        ``is_fatal`` instead: a recoverable hiccup sets this while the loop goes
        on to answer correctly, so treating it as failure means discarding a
        real reply — or announcing a breakdown that did not happen.
        """
        return self.error is not None

    #: Severities that still leave the turn with something worth showing:
    #: ``recoverable`` (absorbed mid-loop, the agent answered anyway) plus the
    #: two verdicts ON a fatal. Treating any of these as fatal discards a reply
    #: the user is entitled to see.
    _NON_FATAL_SEVERITIES = ("recoverable", *VERDICT_ON_FATAL_SEVERITIES)

    @property
    def is_fatal(self) -> bool:
        """The turn has no usable output.

        Not simply "an error happened": three of the four severities describe a
        turn that produced a reply anyway, and only ``fatal`` (plus an
        unlabelled error, treated as the worse case since presenting a
        possibly-empty turn as a success is the more harmful direction) means
        there is nothing to show.
        """
        if self.error is None:
            return False
        return self.error.severity not in self._NON_FATAL_SEVERITIES


class RunErrorTracker:
    """A run's error verdict, fed one runtime message at a time.

    The ONE rule for "which failure does this run end with, and does it
    leave the turn without output": ``collect_run`` folds it into its
    ``RunCollection``; ``InProcessAgentRuntimeClient.run_stream`` — which
    yields events and never builds a collection — uses the same object to
    settle a circuit-breaker probe, so a streaming turn and a collected turn
    can never disagree about whether they failed.

    Last error wins, except that fatality is sticky (see ``error``).
    """

    def __init__(self) -> None:
        self._last: Optional[RunError] = None
        self._saw_fatal = False

    def observe(self, msg: Any) -> None:
        """Feed one runtime message; everything but ``ERROR`` is ignored."""
        if getattr(msg, "message_type", None) != MessageType.ERROR:
            return
        severity = str(getattr(msg, "severity", "") or "")
        self._last = RunError(
            error_type=getattr(msg, "error_type", "unknown"),
            error_message=getattr(msg, "error_message", str(msg)),
            severity=severity,
        )
        # Fatality is sticky, because "last error wins" is the wrong rule
        # for it: a run that hits a fatal and then emits a recoverable
        # follow-up frame is still a run with no usable output, and letting
        # the later frame overwrite the verdict would present a broken turn
        # as a working one.
        if severity not in RunCollection._NON_FATAL_SEVERITIES:
            self._saw_fatal = True

    @property
    def error(self) -> Optional[RunError]:
        """The run's error so far (None when none arrived).

        A fatal seen anywhere in the run outranks a LESS informed last frame
        — but not a MORE informed one. `recovered` and
        `recovered_after_reply` are only ever emitted BECAUSE a fatal
        happened; they are the verdict on that fatal ("the fallback answered
        anyway", "the agent had already spoken"), not a competing claim
        about it. Upgrading them here would undo the only thing they exist
        to say, and the turn's real reply would be replaced by a failure
        notice.

        What the rule is actually for: a `recoverable` frame arriving after a
        fatal, where the later frame knows less, not more.

        `""` is exempt for a different reason than the verdicts: it already
        READS as fatal (`is_fatal` takes the worse side for anything
        unlabelled), so stamping it would change nothing except to destroy
        the one thing the empty string carries — that the runtime did not
        say.
        """
        error = self._last
        if (
            error is not None
            and self._saw_fatal
            and error.severity not in ("", "fatal", *VERDICT_ON_FATAL_SEVERITIES)
        ):
            return RunError(
                error_type=error.error_type,
                error_message=error.error_message,
                severity="fatal",
            )
        return error

    @property
    def is_fatal(self) -> bool:
        """Same answer as ``RunCollection.is_fatal`` for this error."""
        return RunCollection(error=self.error).is_fatal


async def collect_run(
    runtime,
    *,
    agent_id: str,
    user_id: str,
    input_content: str,
    working_source,
    on_progress: Optional[Callable[[str, Optional[str]], Awaitable[None]]] = None,
    on_event_id: Optional[Callable[[str], Awaitable[None]]] = None,
    include_monologue: bool = False,
    segments_sink: Optional[list] = None,
    **extra_kwargs,
) -> RunCollection:
    """Drive ``runtime.run(...)`` to completion and group its output.

    Any keyword argument accepted by ``AgentRuntime.run`` can be passed
    through ``extra_kwargs`` (e.g. ``trigger_extra_data``,
    ``job_instance_id``, ``forced_narrative_id``, ``pass_mcp_servers``,
    ``cancellation``).

    ``on_progress(kind, tool_name)`` — optional, opt-in — is awaited once per
    observed message with ``kind`` in {"thinking","tool","response","error"}
    (``tool_name`` set only for "tool"). Used to mirror a live "what is this
    agent doing" status (e.g. the team-chat activity view). It must never raise;
    any exception is swallowed so status reporting can't break the run.

    ``segments_sink`` — optional — is accumulated INTO as the run streams, so a
    caller holding the same list can read the monologue/reply boundary before
    the run returns. The team room needs that: its post happens inside the turn
    (the chat rows are written before ``run()`` returns, so a post made after it
    is not recorded as a reply), and by the time the deliverer is called the
    reply's deltas have all been seen. Entries are in the accumulator's
    ``{kind, parts}`` form — join them with ``joined_segments``.

    ``on_event_id(event_id)`` — optional, opt-in — is awaited at most once, as
    soon as the Step-0 progress message's ``details.event_id`` is observed.
    Used to bind the turn's events-row id onto a live status row (e.g.
    ``TurnActivity.note_event_id``) as soon as it's known, rather than waiting
    for the whole run to finish. Like ``on_progress``, it must never raise;
    any exception is swallowed so status reporting can't break the run.

    ``include_monologue`` — opt-in — folds NexusPower monologue segments
    (``AGENT_THINKING.monologue``) into ``output_text``. ONLY for callers
    whose prompt tells the agent its plain text is delivered (today: bus
    team rooms, whose replies auto-post to the shared room). Everywhere
    else the monologue contract promises the agent its plain text is never
    DELIVERED — its owner may watch it as working narration, but it is
    addressed to no one. Relaying it to an inbox or an A2A response would
    deliver deliberation the agent never addressed to anyone. Visible is not
    the same as delivered, and it is delivery this guards.
    """
    text_parts: list[str] = []
    # The live accumulator. When the caller passes a sink, it IS the list, so a
    # reader holding that reference sees segments as they arrive — which is what
    # the team-room deliverer needs, being called during the run rather than
    # after it.
    segments: list[dict] = segments_sink if segments_sink is not None else []
    tool_calls: list[str] = []
    raw_items: list[Any] = []
    errors = RunErrorTracker()
    event_id: Optional[str] = None
    # Dedup synthesized tool_call_items by (tool_name, arguments_json). With
    # include_partial_messages=True the same ToolUseBlock can surface across
    # multiple AssistantMessage frames — the SDK dedups by tool_call_id, but
    # that id isn't propagated into the ProgressMessage we observe here.
    # Dedup defensively so Lark doesn't echo the same reply twice in the inbox.
    import json as _json
    seen_tool_calls: set[str] = set()

    async for msg in runtime.run(
        agent_id=agent_id,
        user_id=user_id,
        input_content=input_content,
        working_source=working_source,
        **extra_kwargs,
    ):
        mt = getattr(msg, "message_type", None)
        # NexusPower: the agent's plain text streams as thinking with the
        # ``monologue`` subset set — the assistant text the claude driver
        # would emit as AGENT_RESPONSE. Only meaningful when the caller
        # opted in (see include_monologue docstring); provider CoT arrives
        # with monologue="" and never counts.
        monologue = (
            getattr(msg, "monologue", "")
            if include_monologue and mt == MessageType.AGENT_THINKING
            else ""
        )
        if mt == MessageType.AGENT_RESPONSE:
            delta = getattr(msg, "delta", None)
            if delta:
                text_parts.append(delta)
                _add_segment(segments, "reply", delta)
        elif mt == MessageType.AGENT_THINKING:
            if monologue:
                text_parts.append(monologue)
                _add_segment(segments, "monologue", monologue)
        elif mt == MessageType.TOOL_CALL:
            name = getattr(msg, "tool_name", None)
            if name:
                tool_calls.append(name)
        elif mt == MessageType.ERROR:
            # Last error wins — keep the most specific failure the run
            # reached (typically there's only one, but AgentRuntime may
            # yield a generic + specific pair in edge cases). Fatality is
            # sticky; the tracker owns both rules.
            errors.observe(msg)

        # Raw payload on any message type (Lark needs it from TOOL_CALL
        # events; other triggers simply ignore the list).
        raw = getattr(msg, "raw", None)
        if raw is not None:
            raw_items.append(raw)
        else:
            # Tool calls arrive as ProgressMessage with details.tool_name;
            # there's no raw attribute. Synthesize one in the shape Lark's
            # extractor expects so inbox rows get the real reply instead of
            # the "(Replied on Lark)" fallback.
            details = getattr(msg, "details", None)
            if isinstance(details, dict) and details.get("tool_name"):
                tool_name = details["tool_name"]
                arguments = details.get("arguments", {})
                try:
                    args_key = _json.dumps(arguments, sort_keys=True, default=str)
                except Exception:
                    args_key = repr(arguments)
                dedup_key = f"{tool_name}::{args_key}"
                if dedup_key in seen_tool_calls:
                    continue
                seen_tool_calls.add(dedup_key)
                raw_items.append({
                    "item": {
                        "type": ITEM_TYPE_TOOL_CALL,
                        "tool_name": tool_name,
                        "arguments": arguments,
                    }
                })
                if tool_name not in tool_calls:
                    tool_calls.append(tool_name)

        # Live progress mirror (opt-in). Detect the tool name from either the
        # typed attr or the ProgressMessage details, then classify the phase.
        if on_progress is not None:
            _tool = getattr(msg, "tool_name", None)
            if not _tool:
                _d = getattr(msg, "details", None)
                if isinstance(_d, dict):
                    _tool = _d.get("tool_name")
            # A monologue-carrying thinking frame is the agent SPEAKING —
            # but only on opted-in surfaces, where that text really is
            # delivered (``monologue`` is already "" otherwise). Report it
            # as "response" so activity views don't show "thinking" while
            # the room reply is being written.
            kind = (
                "tool" if _tool
                else "response" if (
                    mt == MessageType.AGENT_RESPONSE or monologue
                )
                else "thinking" if mt == MessageType.AGENT_THINKING
                else "error" if mt == MessageType.ERROR
                else None
            )
            if kind:
                try:
                    await on_progress(kind, _tool)
                except Exception:  # noqa: BLE001 — status must never break the run
                    pass

        # Step-0 event_id capture (opt-in via on_event_id). Fires at most once —
        # the first Step-0 completion wins, and it's the only place this id
        # is ever surfaced.
        if event_id is None:
            details = getattr(msg, "details", None)
            candidate = details.get("event_id") if isinstance(details, dict) else None
            if candidate:
                event_id = str(candidate)
                if on_event_id is not None:
                    try:
                        await on_event_id(event_id)
                    except Exception:  # noqa: BLE001 — status must never break the run
                        logger.opt(exception=True).warning("on_event_id callback failed")

    error = errors.error

    # A turn whose whole output is blank is dropped upstream (`if response_text`);
    # the segments must agree rather than resurrect an empty bubble.
    if not "".join(text_parts).strip():
        segments = []

    # Parts are an accumulation detail; the contract is `{kind, text}`.
    final_segments = joined_segments(segments)

    return RunCollection(
        output_text="".join(text_parts),
        segments=final_segments,
        tool_calls=tool_calls,
        raw_items=raw_items,
        error=error,
        event_id=event_id,
    )
