"""
@file_name: loop.py
@author: Bin Liang
@date: 2026-07-29
@description: NexusPowerLoop — the phase machine. It decides "what
happens next" and nothing else; every fork is a strategy call, every
capability is a channel call. The extension roadmap requires zero edits
here — that is the design's core promise (and why the ≤500-line review
gate is sustainable).

Phases per step: PROJECT (compaction check + context projection) →
MODEL_STREAM (typed events out of the model client; argument-field
streaming for declared tools) → DISPATCH (policy-checked execution) →
DRAIN_STEERING (v1: always empty) → STOP_CHECK (v1: no actions = stop).

Hard guarantees:
  - cancellation lands at safe boundaries and NEVER splits a
    tool_use/result pair (synthesis closes open calls);
  - every termination path emits exactly one ``turn_done`` with real
    cumulative usage (the billing chain's sole source);
  - a ``CONTEXT_OVERFLOW`` classification triggers compaction + step
    retry (progress-guarded), so long turns never die on the context
    wall;
  - iron rule #14: no iteration/duration ceiling exists in this file.
"""

from __future__ import annotations

from typing import Any, AsyncIterator

from loguru import logger

from xyz_agent_context.agent_framework.nexus_power.contracts.errors import (
    ErrorType,
    LoopError,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.events import (
    TYPE_ERROR,
    TYPE_TEXT_DELTA,
    TYPE_THINKING_DELTA,
    EndReason,
    LoopEvent,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.model import (
    ModelRequest,
    ProviderMessage,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    ToolCall,
    ToolResult,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.harness.hooks import (
    HookEvent,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.arg_stream import (
    StreamingArgExtractor,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.compaction import (
    estimate_message_tokens,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.modeling.prompt_cache import (
    plan_cache,
)
from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.session.turn_ledger import (
    TurnLedger,
)


# Sent only after a backend has actually refused a trailing assistant
# message. Worded to buy back what the repair costs: the model is being
# asked to resume its own sentence, so it must not restate the part the
# user already has.
CONTINUE_PREFILL = (
    "Continue your previous message exactly where it stopped. "
    "Do not repeat any earlier content and do not add any preamble — "
    "output only the continuation."
)


class NexusPowerLoop:
    """One instance runs one turn (no cross-turn state: stateless worker)."""

    def __init__(self, assembly: Any, ledger: TurnLedger) -> None:
        self._a = assembly
        self._ledger = ledger
        self._closed = False
        self._continuation_turn = False  # prefill repair, armed at most once

    async def run_turn(self) -> AsyncIterator[LoopEvent]:
        a, ledger = self._a, self._ledger
        try:
            while True:
                # ---- boundary: cancellation --------------------------------
                if a.cancel.requested():
                    async for ev in self._interrupt("cancelled by user"):
                        yield ev
                    return

                # ---- PROJECT ----------------------------------------------
                if a.compaction.should_compact(ledger, a.model.profile):
                    async for ev in self._compact("proactive"):
                        yield ev
                request = self._build_request()

                # ---- MODEL_STREAM (with overflow-compaction retry) --------
                step_calls: list[ToolCall] = []
                step_meta: dict[str, str] = {}
                attempt = 0
                while True:
                    step_calls, error = [], None
                    step_meta.clear()
                    try:
                        async for ev in self._stream_step(request, step_calls, step_meta):
                            yield ev
                    except Exception as exc:  # noqa: BLE001 - classified below
                        error = a.errors.classify(exc)
                    if error is None:
                        break
                    # The failed attempt may have streamed text and tool
                    # calls into the ledger before it broke. Every path
                    # below either retries or fails, and both need the
                    # ledger back at the state the attempt started from —
                    # otherwise the replay duplicates the text and trips
                    # the duplicate-call_id guard.
                    ledger.discard_step()
                    if error.error_type is ErrorType.CONTEXT_OVERFLOW:
                        compacted = False
                        async for ev in self._compact("reactive"):
                            compacted = True
                            yield ev
                        if compacted:
                            request = self._build_request()
                            continue  # retry the step with a smaller context
                        # Nothing left to compact: retrying the same
                        # oversized request would only burn money, so
                        # surface the actionable error instead.
                        async for ev in self._fail(error):
                            yield ev
                        return
                    if (
                        error.error_type is ErrorType.PREFILL_REJECTED
                        and not self._continuation_turn
                    ):
                        # The turn ends mid-assistant-message and this
                        # backend refuses to continue one. Ask for the
                        # continuation explicitly and replay the step;
                        # the user sees one uninterrupted turn.
                        #
                        # Armed once — re-appending the instruction every
                        # round would be a spin loop, and each copy
                        # compounds it. That is a bound on the REPAIR,
                        # not a verdict on the error: most of these
                        # rejections are about which upstream backend
                        # answered, so the retry policy still gets its
                        # turn afterwards (see the classifier).
                        self._continuation_turn = True
                        request = self._build_request()
                        continue
                    attempt += 1
                    if error.retryable and await a.retry.should_retry(error, attempt):
                        continue
                    async for ev in self._fail(error):
                        yield ev
                    return

                # ---- boundary: cancellation (post-stream) -----------------
                # Catches both shapes: cancel arrived mid-stream (the
                # stream loop broke out early) and cancel arrived between
                # the stream's end and dispatch. Without this, an aborted
                # text-only stream would fall through STOP_CHECK and close
                # as NO_MORE_ACTIONS — a user interrupt masquerading as a
                # natural stop.
                if a.cancel.requested():
                    async for ev in self._interrupt("cancelled by user"):
                        yield ev
                    return

                # ---- DISPATCH ---------------------------------------------
                for call in step_calls:
                    if a.cancel.requested():
                        async for ev in self._interrupt("cancelled by user"):
                            yield ev
                        return
                    if call.parse_error is not None:
                        # Codex-shape recovery: a call whose argument JSON
                        # never parsed is ANSWERED, not executed — no
                        # hooks, no channel. The error names the real
                        # cause (a "length" stop means the output-token
                        # limit cut the arguments) so the model can
                        # split the work instead of chasing the tool's
                        # misleading downstream failure.
                        result = unparsed_call_result(
                            call,
                            stop_reason=step_meta.get("stop_reason", ""),
                            max_output_tokens=a.model.profile.max_output_tokens,
                        )
                        for ev in ledger.record_tool_result(call.id, result):
                            yield await self._log(ev)
                        continue
                    outcome = await a.hooks.fire(
                        HookEvent.PRE_TOOL_USE, {"call": call}
                    )
                    if not outcome.allowed:
                        result = call_denied_by_hook(call, outcome.notes)
                    else:
                        try:
                            result = await a.tools.execute(call)
                        except Exception as exc:  # noqa: BLE001
                            # A channel that raises must still produce a
                            # RESULT. Letting the exception fly left the
                            # call open on the ledger, and the turn then
                            # closed with a dangling `tool_calls` entry —
                            # breaking the one invariant this loop swears
                            # to: tool_use and tool_result are never
                            # separated (2026-07-29 review).
                            logger.exception(
                                f"tool channel raised for {call.name!r}: {exc}"
                            )
                            result = ToolResult(
                                call_id=call.id,
                                ok=False,
                                error=f"tool execution failed: {exc}",
                            )
                    await a.hooks.fire(
                        HookEvent.POST_TOOL_USE, {"call": call, "result": result}
                    )
                    for ev in ledger.record_tool_result(call.id, result):
                        yield await self._log(ev)
                    # Drain events produced by the channel itself (plan
                    # updates today) — recorded on the ledger already,
                    # so this only surfaces them on the stream.
                    while a.side_events:
                        yield await self._log(a.side_events.pop(0))

                # ---- DRAIN_STEERING ---------------------------------------
                injected = await a.steering.drain()
                if injected:
                    ledger.record_steering(injected)
                    continue

                # ---- STOP_CHECK -------------------------------------------
                if await a.stop.should_stop(step_calls, ledger):
                    await a.hooks.fire(HookEvent.STOP, {"steps": ledger.num_steps()})
                    yield await self._close(EndReason.NO_MORE_ACTIONS)
                    return
        finally:
            if not self._closed:
                # Belt-and-braces: no path may leave the turn unclosed
                # (turn_done is the billing chain's sole source).
                logger.warning("loop exited without closure; emitting turn_done")
                await self._log(
                    self._ledger.close_turn(EndReason.ERROR, model=self._a.params.model)
                )

    # ------------------------------------------------------------------
    # Phase helpers (no business logic beyond orchestration)
    # ------------------------------------------------------------------

    def _build_request(self) -> ModelRequest:
        a = self._a
        messages = a.projector.project(self._ledger, a.model.profile)
        if self._continuation_turn and _ends_with_assistant(messages):
            # Transport repair, not turn history: it exists to satisfy a
            # backend that rejects a trailing assistant message, so it
            # stays out of the ledger and never reaches the next turn.
            #
            # Gated on the shape it repairs, not just on the flag. Once
            # the turn moves on, the projection ends in a tool result and
            # this instruction would be a lie that actively suppresses
            # the model's normal post-tool narration — and, in the
            # Anthropic dialect where tool results ride in user messages,
            # would stack two user turns in a row.
            messages = [*messages, {"role": "user", "content": CONTINUE_PREFILL}]
        tools = [spec.as_openai_tool() for spec in a.tools.visible_tools()]
        return ModelRequest(
            messages=messages,
            tools=tools,
            params=a.params,
            cache_plan=plan_cache(messages, a.model.profile),
            # The measurement is better but is zero on the turn's first
            # step, which is exactly the step whose projection already
            # carries every earlier turn — so take whichever is larger.
            input_tokens_estimate=max(
                self._ledger.last_input_tokens(), estimate_message_tokens(messages)
            ),
        )

    async def _stream_step(
        self,
        request: ModelRequest,
        step_calls: list[ToolCall],
        step_meta: dict[str, str] | None = None,
    ) -> AsyncIterator[LoopEvent]:
        a, ledger = self._a, self._ledger
        extractors: dict[int, StreamingArgExtractor] = {}
        stream_meta: dict[int, tuple[str, str, bool]] = {}  # index -> (call_id, name, expressive)
        stream = a.model.stream_step(request)
        async for model_event in stream:
            if a.cancel.requested():
                # Abort the provider stream at the earliest observable
                # point, EXPLICITLY: a bare break leaves the generator
                # (and its HTTP stream) alive until garbage collection —
                # paying for and waiting on tokens nobody will read.
                # The post-stream boundary in run_turn closes the turn
                # as INTERRUPTED; the deltas already streamed stay on
                # the ledger.
                await stream.aclose()
                break
            kind = model_event.kind
            if kind == "tool_use_start":
                if a.include_arg_deltas:
                    index = int(model_event.payload.get("call_index", 0))
                    name = str(model_event.payload.get("tool_name", ""))
                    spec = a.tools.spec_for(name)
                    fields = spec.annotations.streamable_fields if spec else ()
                    # An expression tool's streamed argument IS the reply
                    # being written — the adapter promotes those fragments
                    # to reply deltas so the user reads it live.
                    expressive = a.expression.is_expressive(name)
                    if not fields and expressive:
                        fields = a.reply_fields
                    if fields:
                        extractors[index] = StreamingArgExtractor(index, tuple(fields))
                        stream_meta[index] = (
                            str(model_event.payload.get("call_id", "")),
                            name,
                            expressive,
                        )
                # No continue: the event also reaches the ledger, which
                # emits the name-first ui event so the frontend can show
                # "using X" before the arguments finish streaming.
            if kind == "arg_delta":
                index = int(model_event.payload.get("call_index", 0))
                extractor = extractors.get(index)
                if extractor is not None:
                    for delta in extractor.feed(str(model_event.payload.get("text", ""))):
                        yield await self._log(
                            self._arg_delta_event(delta, stream_meta.get(index))
                        )
                continue
            if kind == "done" and step_meta is not None:
                step_meta["stop_reason"] = str(
                    model_event.payload.get("stop_reason", "")
                )
            events = ledger.record_model_event(model_event)
            if kind == "tool_use":
                payload = model_event.payload
                step_calls.append(
                    ToolCall(
                        id=str(payload["call_id"]),
                        name=str(payload["tool_name"]),
                        args=dict(payload.get("args") or {}),
                        parse_error=payload.get("parse_error"),
                        truncated=bool(payload.get("args_truncated")),
                    )
                )
                index = int(model_event.content_index)
                extractor = extractors.get(index)
                if extractor is not None:
                    meta = stream_meta.get(index)
                    if meta and not meta[0]:  # call_id only known now
                        stream_meta[index] = (str(payload["call_id"]), meta[1], meta[2])
                    for delta in extractor.finalize(dict(payload.get("args") or {})):
                        yield await self._log(
                            self._arg_delta_event(delta, stream_meta.get(index))
                        )
            for ev in events:
                yield await self._log(ev)

    def _arg_delta_event(
        self, delta: Any, meta: tuple[str, str, bool] | None
    ) -> LoopEvent:
        call_id, tool_name, expressive = meta or ("", "", False)
        return self._ledger.record_arg_field_delta(
            delta.call_index,
            delta.field_path,
            delta.text,
            call_id=call_id,
            tool_name=tool_name,
            expressive=expressive,
        )

    async def _compact(self, mode: str) -> AsyncIterator[LoopEvent]:
        a, ledger = self._a, self._ledger
        await a.hooks.fire(HookEvent.PRE_COMPACT, {"mode": mode})
        entries = await a.compaction.compact(ledger, a.model.profile)
        if entries:
            logger.info(f"compaction ({mode}): {len(entries)} replacement entries")
            for ev in ledger.apply_compaction(entries):
                yield await self._log(ev)
        await a.hooks.fire(HookEvent.POST_COMPACT, {"count": len(entries)})

    async def _interrupt(self, reason: str) -> AsyncIterator[LoopEvent]:
        for ev in self._ledger.synthesize_interrupted_results(reason):
            yield await self._log(ev)
        yield await self._close(EndReason.INTERRUPTED)

    async def _fail(self, error: LoopError) -> AsyncIterator[LoopEvent]:
        for ev in self._ledger.synthesize_interrupted_results(
            f"step failed: {error.error_type.value}"
        ):
            yield await self._log(ev)
        yield await self._log(
            LoopEvent(
                track="ui",
                seq=-1,  # transient; not a ledger row (the ledger owns seqs)
                type=TYPE_ERROR,
                payload={
                    "error_type": error.error_type.value,
                    "message": error.message,
                    "retryable": error.retryable,
                },
            )
        )
        yield await self._close(EndReason.ERROR)

    async def _close(self, reason: EndReason) -> LoopEvent:
        self._closed = True
        # Price the turn here: a self-driven loop must report its own
        # cost or every turn shows $0 on the platform's cost surface
        # (the claude CLI reports its own; nobody reports ours).
        model = self._a.params.model
        try:
            cost = self._a.model.estimate_cost_usd(self._ledger.total_usage(), model)
        except Exception as exc:  # noqa: BLE001 - pricing never breaks a turn
            logger.warning(f"cost estimation failed: {exc}")
            cost = None
        return await self._log(
            self._ledger.close_turn(reason, model=model, cost_usd=cost)
        )

    async def _log(self, event: LoopEvent) -> LoopEvent:
        if event.type in (TYPE_TEXT_DELTA, TYPE_THINKING_DELTA):
            event = self._a.expression.tag_text_event(event)
        await self._a.log.append(event)  # logging is pass-through, not a fork
        return event


def call_denied_by_hook(call: ToolCall, notes: tuple[str, ...]):
    return ToolResult(
        call_id=call.id,
        ok=False,
        error="denied by hook: " + ("; ".join(notes) or "vetoed"),
    )


# Stop reasons that admit the output cap severed the response. Kept as a
# corroborating signal only — see ``unparsed_call_result``.
_TRUNCATING_STOP_REASONS = frozenset({"length", "max_tokens"})



def _ends_with_assistant(messages: list[ProviderMessage]) -> bool:
    return bool(messages) and messages[-1].get("role") == "assistant"




def unparsed_call_result(
    call: ToolCall, *, stop_reason: str, max_output_tokens: int
) -> ToolResult:
    """The answer for a call whose argument JSON never parsed.

    Wording is tool-agnostic (iron rule #4) and states the recovery
    path explicitly — weaker models do not reliably self-repair from a
    bare parse error (measured across the industry; codex#19765).

    Which recovery to name is decided PRIMARILY by the received bytes
    (``call.truncated``), and only secondarily by ``stop_reason``. The
    two answers are opposites — send it smaller versus send it correct —
    so naming the wrong one is worse than saying nothing: a gateway that
    reported ``tool_use`` for a severed call had the model hunting an
    escaping bug and re-sending the same oversized arguments until the
    turn died (agent_560a2bf191ba, dev 2026-07-30). ``stop_reason`` stays in
    as corroboration because a truthful provider reports it before the
    JSON is even suspicious, but it can no longer veto the evidence.
    """
    if call.truncated or stop_reason in _TRUNCATING_STOP_REASONS:
        error = (
            f"arguments truncated ({call.parse_error}): the argument JSON "
            "stops mid-value, so the response ended before the arguments "
            "finished streaming — normally the output token limit "
            f"({max_output_tokens}). The tool was NOT executed. Re-issue "
            "the call with less content per call — produce the output in "
            "smaller pieces across multiple calls, or use an editing tool "
            "to extend existing content incrementally."
        )
    else:
        error = (
            f"malformed JSON arguments ({call.parse_error}). The tool was "
            "NOT executed. Re-emit the complete call as one valid JSON "
            "object."
        )
    return ToolResult(call_id=call.id, ok=False, error=error)
