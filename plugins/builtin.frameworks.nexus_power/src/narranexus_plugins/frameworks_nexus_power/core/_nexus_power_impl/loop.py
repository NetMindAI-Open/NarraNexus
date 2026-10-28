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
DRAIN_STEERING (default NullSteeringInlet is empty; QueueSteeringInlet
is the live source, transport not yet wired) → STOP_CHECK (v1: no
actions = stop).

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

from narranexus_plugins.frameworks_nexus_power.core.contracts.errors import (
    ErrorType,
    LoopError,
)
from narranexus_plugins.frameworks_nexus_power.core.contracts.events import (
    TYPE_ERROR,
    TYPE_STEER_CONSUMED,
    TYPE_TEXT_DELTA,
    TYPE_THINKING_DELTA,
    EndReason,
    LoopEvent,
)
from narranexus_plugins.frameworks_nexus_power.core.contracts.model import (
    ModelRequest,
    ProviderMessage,
)
from narranexus_plugins.frameworks_nexus_power.core.contracts.tooling import (
    ToolCall,
    ToolResult,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.harness.hooks import (
    HookEvent,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.arg_stream import (
    StreamingArgExtractor,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.compaction import (
    estimate_message_tokens,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.profiles import (
    requested_max_tokens,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.modeling.prompt_cache import (
    plan_cache,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.prompts.library import (
    NexusPowerPrompts,
)
from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.session.turn_ledger import (
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

# Stands in for each image once the provider has refused image input. It
# states a transport fact (the model cannot see this image) so the agent
# can report the limitation instead of claiming to have seen it.
IMAGE_WITHHELD_NOTE = (
    "[image omitted: the configured model rejected image input, so this "
    "image cannot be shown to you]"
)


class NexusPowerLoop:
    """One instance runs one turn (no cross-turn state: stateless worker)."""

    def __init__(self, assembly: Any, ledger: TurnLedger) -> None:
        # `assembly` is intentionally `Any`: typing it `LoopAssembly` would put
        # every `a.<seat>` access under the checker, which surfaces pre-existing
        # protocol-completeness gaps unrelated to this change (ExpressionPolicy
        # has no `names`, ToolExecutor no `spec_for`, stream_step returns a bare
        # AsyncIterator with no `aclose`). Tightening it is worth doing but is its
        # own PR — see reference/self_notebook/todo. So the loop's `a.wait.pending`
        # is NOT statically checked here (only LoopAssembly's construction site
        # is); a producer must not assume it is, and must clamp through
        # WaitRequest.request rather than trust a raw write (see WaitState).
        self._a = assembly
        self._ledger = ledger
        self._closed = False
        self._continuation_turn = False  # prefill repair, armed at most once
        self._turn_expressed = False     # any expressive call seen this turn
        # Any COMMITTED text on a step that ran with no expression tool.
        self._turn_text_streamed = False
        self._expression_nudged = False  # mute-turn nudge, armed at most once
        self._truncation_retried = False  # output-budget doubling, armed at most once
        self._images_withheld = False  # image-rejection repair, armed at most once
        # Floor multiplier for the NEXT ``_build_request()``, carried on
        # ``ModelRequest.floor_multiplier`` — never written into the shared
        # ``a.params.extra`` dict. Raised to 2 only for the one step the
        # output-budget-truncation retry replays, then reset to 1.
        self._truncation_floor_multiplier = 1

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
                        # Delivery is sampled when the step COMMITS, never
                        # when a delta arrives: text from an attempt that
                        # later broke is thrown away by ``discard_step()``
                        # below and was never delivered. The expression
                        # contract is read at the same moment because
                        # ``expand()`` can grant delivery tools mid-turn
                        # (DISPATCH runs after this point); only on a step
                        # with NO expression tool is the plain text the
                        # delivered reply — with one, text is monologue
                        # nobody receives. See ``_fail``.
                        if step_meta.get("had_text") == "1" and not a.expression.names():
                            self._turn_text_streamed = True
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
                    if (
                        error.error_type is ErrorType.IMAGE_INPUT_REJECTED
                        and not self._images_withheld
                        and _has_images(request.messages)
                    ):
                        # The user's model is text-only (their choice,
                        # binding rule #15). Withhold images for the rest
                        # of the turn — every later screenshot would draw
                        # the same deterministic 400 — and replay the
                        # step so one browser_look cannot end the turn.
                        logger.warning(
                            "model {} rejected image input; withholding images this turn",
                            a.params.model,
                        )
                        self._images_withheld = True
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

                # ---- OUTPUT-BUDGET TRUNCATION -------------------------------
                # A step that came back with zero text and zero tool calls
                # AND a stop_reason admitting the output cap severed the
                # response is not "no more actions" — it is a thinking-
                # by-default model that spent its entire ``max_tokens`` on
                # hidden chain-of-thought before it could reach an answer
                # (measured 2026-09-08 against NetMind's DeepSeek-V4-Pro).
                # Left alone this falls through STOP_CHECK and closes as
                # NO_MORE_ACTIONS with a real reply the model never got to
                # write — a silent empty run. Double the floor and replay
                # the step ONCE (armed via ``_truncation_retried``, same
                # "repair the request, not a verdict on the error" idiom as
                # PREFILL_REJECTED) — but ONLY if doubling can actually ask
                # for more: a model whose ceiling already sits at (or a
                # user override that already pins) the current budget would
                # replay a byte-identical request and 100%-certainly fail
                # again, burning a full extra round-trip for nothing.
                # If the doubled budget still
                # yields nothing, a third try would not either, so surface
                # a real, terminal failure instead of retrying forever.
                #
                # This check sits before DRAIN_STEERING and the replay
                # ``continue``s straight back to PROJECT, so a steering
                # message that arrived during the truncated step is drained
                # one model call later (after the replayed step). Accepted:
                # the replay is at most one extra call per turn and the
                # message is not lost, only deferred.
                stop_reason = step_meta.get("stop_reason", "")
                produced_output = bool(step_calls) or step_meta.get("had_text") == "1"
                if not produced_output and stop_reason in _TRUNCATING_STOP_REASONS:
                    # One source of truth for "what this request sent":
                    # the same function the model client used, so a pinned
                    # ``params.extra['max_tokens']`` (which always wins)
                    # makes both values equal and rules the replay out.
                    current_budget = requested_max_tokens(
                        a.model.profile,
                        request.params.extra,
                        request.input_tokens_estimate,
                        floor_multiplier=request.floor_multiplier,
                    )
                    next_multiplier = request.floor_multiplier * 2
                    new_budget = requested_max_tokens(
                        a.model.profile,
                        request.params.extra,
                        request.input_tokens_estimate,
                        floor_multiplier=next_multiplier,
                    )
                    if not self._truncation_retried and new_budget > current_budget:
                        self._truncation_retried = True
                        self._truncation_floor_multiplier = next_multiplier
                        # The empty step folded to nothing in
                        # `_turn_messages` (`_fold_step_message` no-ops
                        # when text+calls are both empty), but its CoT is
                        # NOT nothing: `_step_thinking` accumulated the
                        # whole thinking-exhausted budget and that early
                        # return does not clear it. Left alone, the
                        # replayed step's OWN thinking would be appended
                        # onto this abandoned CoT and folded into one
                        # `reasoning_content` — a concatenation, not the
                        # CoT that actually produced the replay's tool
                        # call. `discard_step()`
                        # clears all three step buffers and is a no-op on
                        # the already-empty text/calls, so it only ever
                        # fixes the leaked thinking here.
                        ledger.discard_step()
                        continue  # the outer loop rebuilds the request
                    async for ev in self._fail(
                        LoopError(
                            ErrorType.OUTPUT_TRUNCATED,
                            "model output truncated: thinking exhausted the "
                            f"output budget (max_tokens={current_budget})",
                        )
                    ):
                        yield ev
                    return
                # The boosted floor served exactly the step it was armed
                # for. Every later step gets the plain floor back: the
                # floor deliberately wins over headroom (see
                # ``output_budget``), so a multiplier left in place would
                # keep pushing ``input + max_tokens`` past the wall on
                # every later, larger-input step of this turn.
                # ``_truncation_retried`` stays armed, so a later
                # truncation fails instead of retrying again.
                self._truncation_floor_multiplier = 1

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

                # Expression accounting rides the contract's own
                # adjudicator, on parse-VALID calls only: a reply call
                # whose argument JSON was truncated is answered, not
                # executed — nothing was delivered, so it must not mark
                # the turn as expressed (pipeline review 2026-08-13
                # Important #1). Hook-denied calls DO count as attempted:
                # nudging a policy-denied tool would push the model to
                # retry a forbidden call. Sits BEFORE the steering drain
                # so a steering `continue` cannot drop a step's calls
                # from the account.
                self._turn_expressed = (
                    self._turn_expressed
                    or a.expression.turn_had_expression(
                        [c for c in step_calls if c.parse_error is None]
                    )
                )

                # Read-and-clear the wait request ONCE, here, before DRAIN. If a
                # steered message arrived THIS step, the non-blocking drain below
                # takes it and `continue`s — that satisfies the wait (the agent
                # asked to wait FOR input; input arrived), so the request must not
                # survive to a later step and then block on stale intent. Clearing
                # it up here is that guarantee; the WAIT boundary uses the local.
                wait_secs = a.wait.pending
                a.wait.pending = None

                # ---- DRAIN_STEERING ---------------------------------------
                injected = await a.steering.drain()
                if injected:
                    ledger.record_steering(injected)
                    # Report which steer_inbox rows were actually CONSUMED, so
                    # the producer advances its cursor on consumption, not on
                    # push (a message pushed but never drained is never acked,
                    # never lost). A transient ui-track signal the transport
                    # intercepts; empty for a source without ids.
                    #
                    # "Consumed" = drained into this turn's ledger, reported
                    # before the model acts on it. The residual vs the push-window
                    # this closed: a subprocess crash between this line flushing
                    # and the model acting would advance the cursor with no output
                    # (at-most-once), and a record_steering raise would buffer the
                    # ids in the inlet un-reported → a later duplicate, not loss.
                    # Both are strictly narrower than the every-turn push-window;
                    # the trigger batch stays at-least-once, steer is at-most-once
                    # across a crash — an asymmetry worth knowing, not fixing here.
                    consumed = a.steering.take_consumed()
                    if consumed:
                        # Yielded DIRECTLY, not through `_log`: this is a transient
                        # control signal the transport intercepts, not a ledger /
                        # NDJSON-truth row — routing it through `_log` would write
                        # a seq=-1 row per drain (dozens a turn) into the file the
                        # event_log docstring says mirrors the future nexus_events
                        # table, and they would collide on the (thread_id, seq)
                        # key. seq=-1 marks "not a ledger row"; the direct yield is
                        # what makes that true.
                        yield LoopEvent(
                            track="ui", seq=-1, type=TYPE_STEER_CONSUMED,
                            payload={"ids": consumed},
                        )
                    continue

                # ---- WAIT_FOR_INPUT ---------------------------------------
                # Opt-in: the agent called `wait_for_input` this step, asking to
                # HOLD the turn open for new input instead of ending it. Reached
                # only if the drain above was EMPTY (a same-step message already
                # satisfied and cleared the request); BEFORE stop (a requested
                # wait must beat the close). `wait_secs` was read-and-cleared once
                # above, so a stale request can never carry to a later step.
                if wait_secs is not None:
                    secs = wait_secs
                    waited = await a.steering.wait_for_input(secs, a.cancel)
                    if waited:
                        ledger.record_steering(waited)  # input arrived -> rides next step
                        # Report consumption too (wait_for_input tracked the ids
                        # via the same _take_one as drain), so a message steered
                        # into a WAITING run advances the producer's cursor — same
                        # signal, same direct-yield, as DRAIN_STEERING above.
                        consumed = a.steering.take_consumed()
                        if consumed:
                            yield LoopEvent(
                                track="ui", seq=-1, type=TYPE_STEER_CONSUMED,
                                payload={"ids": consumed},
                            )
                    elif not a.cancel.requested():
                        # timeout -> tell the agent so it wraps up (a cancel-driven
                        # empty return skips the notice: the cancellation
                        # boundary at the top of the loop will interrupt).
                        ledger.record_steering([{
                            "role": "user",
                            "content": NexusPowerPrompts.wait_timed_out(int(secs)),
                        }])
                    continue

                # ---- STOP_CHECK -------------------------------------------
                if await a.stop.should_stop(step_calls, ledger):
                    # Opt-in mute-turn repair (voice turns): about to close
                    # with ZERO expressive calls while expressive tools
                    # exist -> ONE steering nudge, one more step. Armed at
                    # most once — a model that stays silent after the
                    # nudge closes normally (never a spin loop, and never
                    # a force-stop: this only ADDS a step).
                    if (
                        a.expression_nudge
                        and not self._expression_nudged
                        and not self._turn_expressed
                        and a.expression.names()
                    ):
                        self._expression_nudged = True
                        ledger.record_steering([{
                            "role": "user",
                            "content": NexusPowerPrompts.expression_nudge(
                                a.expression.names()
                            ),
                        }])
                        continue
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
        if self._images_withheld:
            messages = _withhold_images(messages)
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
        tools = [spec.as_openai_tool() for spec in a.tools.model_tools()]
        return ModelRequest(
            messages=messages,
            tools=tools,
            params=a.params,
            cache_plan=plan_cache(messages, a.model.profile),
            # The measurement is better but is zero on the turn's first
            # step, which is exactly the step whose projection already
            # carries every earlier turn — so take whichever is larger.
            floor_multiplier=self._truncation_floor_multiplier,
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
            if kind == "text_delta" and step_meta is not None:
                # Recorded so the OUTPUT-BUDGET TRUNCATION check in
                # run_turn can tell "the model answered but stayed brief"
                # apart from "the model never got past thinking" — both
                # can carry the same stop_reason, and only the second is
                # truncation.
                if model_event.payload.get("text"):
                    step_meta["had_text"] = "1"
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
                    # ``fatal`` contract: see response_processor's
                    # DATA_TYPE_ERROR handling. This is the loop's own
                    # verdict on "delivered nothing": no expressive call
                    # this turn, and no committed plain-text reply (see
                    # where ``_turn_text_streamed`` is set in run_turn).
                    "fatal": not self._turn_delivered(),
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

    def _turn_delivered(self) -> bool:
        """Has this turn put anything in front of its audience yet?"""
        return self._turn_expressed or self._turn_text_streamed

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


def _is_image_part(part: Any) -> bool:
    return isinstance(part, dict) and part.get("type") == "image_url"


def _has_images(messages: list[ProviderMessage]) -> bool:
    return any(
        isinstance(m.get("content"), list) and any(_is_image_part(p) for p in m["content"])
        for m in messages
    )


def _withhold_images(messages: list[ProviderMessage]) -> list[ProviderMessage]:
    """Replace every image part by ``IMAGE_WITHHELD_NOTE`` (a request
    rewrite only — the ledger keeps what the tools returned)."""
    out: list[ProviderMessage] = []
    for message in messages:
        content = message.get("content")
        if isinstance(content, list) and any(_is_image_part(p) for p in content):
            message = {**message, "content": [
                {"type": "text", "text": IMAGE_WITHHELD_NOTE} if _is_image_part(p) else p
                for p in content
            ]}
        out.append(message)
    return out




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
