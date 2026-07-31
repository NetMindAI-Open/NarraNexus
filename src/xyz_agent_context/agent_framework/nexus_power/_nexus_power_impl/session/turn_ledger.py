"""
@file_name: turn_ledger.py
@author: Bin Liang
@date: 2026-07-29
@description: TurnLedger — the turn's single source of truth.

Three invariants are guaranteed BY CONSTRUCTION (no runtime policing —
illegal paths simply do not exist as methods):

  1. tool_use/tool_result pairing — an interruption can only close open
     calls through ``synthesize_interrupted_results``;
  2. role alternation in the projected provider messages — assistant
     blocks fold at step boundaries, tool messages follow their calls;
  3. seq monotonicity — this class is the only seq allocator.

"Entry is schema": ``entries()`` rows persist verbatim as future
``nexus_events`` rows; resume is ``TurnLedger(thread, base=prefix)``.
Compaction only APPENDS ``TYPE_COMPACTION`` replacement entries; the
projected view substitutes replaced spans while the log keeps history.
"""

from __future__ import annotations

from typing import Any, Sequence

from xyz_agent_context.agent_framework.nexus_power.contracts.events import (
    TYPE_COMPACTION,
    TYPE_PLAN,
    TYPE_STEP_DONE,
    TYPE_TEXT_DELTA,
    TYPE_THINKING_DELTA,
    TYPE_TOOL_ARG_DELTA,
    TYPE_TOOL_RESULT,
    TYPE_TOOL_USE,
    TYPE_TOOL_USE_START,
    TYPE_TURN_DONE,
    EndReason,
    LedgerEntry,
    LoopEvent,
    Usage,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.model import (
    ModelEvent,
    ProviderMessage,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.tooling import (
    ToolCall,
    ToolResult,
)

_INTERRUPTED_RESULT_TEXT = "Interrupted: {reason}. This call did not run."


class TurnLedger:
    """Accumulates one turn's events and projects the provider view.

    Satisfies the read-only ``LedgerView`` protocol; write methods are
    called only by the loop (read/write separation by convention of
    injection, enforced by the protocols handed to strategies).
    """

    def __init__(self, thread_id: str, base: Sequence[LedgerEntry] = ()) -> None:
        self._thread_id = thread_id
        self._entries: list[LedgerEntry] = list(base)
        self._seq = (max((e.seq for e in self._entries), default=-1)) + 1
        self._open: dict[str, ToolCall] = {}
        self._usage = Usage()
        self._last_input_tokens = 0
        self._steps = 0
        # Projection state: provider messages appended this turn, plus the
        # seq -> message-index map compaction substitution keys off.
        self._turn_messages: list[ProviderMessage] = []
        self._result_msg_index: dict[int, int] = {}
        self._replacement: dict[int, str] = {}
        # Current-step accumulation (folded into one assistant message at
        # step_done so role alternation holds).
        self._step_text: list[str] = []
        self._step_calls: list[ToolCall] = []

    # ------------------------------------------------------------------
    # Write side (loop only)
    # ------------------------------------------------------------------

    def record_model_event(self, ev: ModelEvent) -> list[LoopEvent]:
        """Record one model event; return the loop events to emit."""
        kind = ev.kind
        if kind == "text_delta":
            text = str(ev.payload.get("text", ""))
            self._step_text.append(text)
            return [self._emit("ui", TYPE_TEXT_DELTA, {"text": text, "monologue": True})]
        if kind == "thinking_delta":
            text = str(ev.payload.get("text", ""))
            return [
                self._emit("ui", TYPE_THINKING_DELTA, {"text": text, "monologue": True})
            ]
        if kind == "tool_use":
            call = ToolCall(
                id=str(ev.payload["call_id"]),
                name=str(ev.payload["tool_name"]),
                args=dict(ev.payload.get("args") or {}),
            )
            if call.id in self._open:
                raise ValueError(f"duplicate tool_use call_id {call.id!r}")
            self._open[call.id] = call
            self._step_calls.append(call)
            return [
                self._emit(
                    "model",
                    TYPE_TOOL_USE,
                    {"call_id": call.id, "tool_name": call.name, "args": call.args},
                )
            ]
        if kind == "done":
            usage = ev.payload.get("usage") or Usage()
            if not isinstance(usage, Usage):
                usage = Usage(**dict(usage))
            self._usage = self._usage + usage
            if usage.input_tokens:
                self._last_input_tokens = usage.input_tokens
            self._fold_step_message()
            self._steps += 1
            return [
                self._emit(
                    "ui",
                    TYPE_STEP_DONE,
                    {
                        "step_index": self._steps - 1,
                        "stop_reason": str(ev.payload.get("stop_reason", "")),
                    },
                    usage=usage,
                )
            ]
        if kind == "tool_use_start":
            # Name-first: tell the UI immediately instead of letting the
            # screen sit blank while the arguments stream. No ledger
            # state — the truth lands with the subsequent tool_use.
            return [
                self._emit(
                    "ui",
                    TYPE_TOOL_USE_START,
                    {
                        "call_id": str(ev.payload.get("call_id", "")),
                        "tool_name": str(ev.payload.get("tool_name", "")),
                    },
                )
            ]
        # arg_delta carries no ledger state in v1; the loop forwards
        # argument-field deltas via record_arg_field_delta.
        return []

    def record_arg_field_delta(
        self,
        call_index: int,
        field_path: str,
        text: str,
        *,
        call_id: str = "",
        tool_name: str = "",
        expressive: bool = False,
    ) -> LoopEvent:
        """A streamed argument-field fragment (ui track only).

        ``expressive`` marks the reply being written live (an expression
        tool's argument) — the adapter turns those into reply deltas and
        drops the rest.
        """
        return self._emit(
            "ui",
            TYPE_TOOL_ARG_DELTA,
            {
                "call_index": call_index,
                "call_id": call_id,
                "tool_name": tool_name,
                "field_path": field_path,
                "text": text,
                "expressive": expressive,
            },
        )

    def record_plan(self, steps: list[dict[str, Any]], note: str = "") -> LoopEvent:
        """The agent's plan snapshot (ui track; replace-on-write).

        Plans live on the ui track because they are presentation and
        re-injected prompt state, not context-rebuilding history — the
        model already sees its plan through the prompt's plan section.
        """
        return self._emit("ui", TYPE_PLAN, {"steps": list(steps), "note": note})

    def record_tool_result(self, call_id: str, result: ToolResult) -> list[LoopEvent]:
        """Pair one result with its open call (unknown/duplicate raises —
        an invariant breach is a programming error, never swallowed)."""
        if call_id not in self._open:
            raise ValueError(f"tool_result for unknown call_id {call_id!r}")
        del self._open[call_id]
        text = result.as_text()
        event = self._emit(
            "model",
            TYPE_TOOL_RESULT,
            {
                "call_id": call_id,
                "ok": result.ok,
                "content": text,
                "error": result.error,
                "synthetic": result.synthetic,
            },
        )
        self._result_msg_index[event.seq] = len(self._turn_messages)
        self._turn_messages.append(
            {"role": "tool", "tool_call_id": call_id, "content": text}
        )
        return [event]

    def discard_step(self) -> None:
        """Throw away the step currently being streamed.

        A retried MODEL_STREAM has to start from the same ledger state as
        the attempt it replaces. Without this, an error arriving MID-
        stream left the first attempt's text and tool calls behind: the
        replay appended its text a second time and re-emitted the same
        ``call_id``, which the duplicate guard in ``record_model_event``
        turns into a hard failure (latent under the v1 ``NoRetry``
        default, immediate once ``StepRetry`` is bound).

        Only the UNCOMMITTED step is affected — ``_fold_step_message``
        has not run, so no message has been produced yet, and calls from
        earlier steps already carry their results.
        """
        for call in self._step_calls:
            self._open.pop(call.id, None)
        self._step_calls = []
        self._step_text = []

    def synthesize_interrupted_results(self, reason: str) -> list[LoopEvent]:
        """Close every open call with a synthetic result so pairing holds
        on any interruption path."""
        events: list[LoopEvent] = []
        for call_id in list(self._open):
            events.extend(
                self.record_tool_result(
                    call_id,
                    ToolResult(
                        call_id=call_id,
                        ok=False,
                        error=_INTERRUPTED_RESULT_TEXT.format(reason=reason),
                        synthetic=True,
                    ),
                )
            )
        return events

    def record_steering(self, messages: Sequence[ProviderMessage]) -> None:
        """Append injected user messages (append-only; P4 consumer)."""
        self._turn_messages.extend(dict(m) for m in messages)

    def apply_compaction(self, entries: Sequence[LedgerEntry]) -> list[LoopEvent]:
        """Append compaction replacement entries and register substitutions.

        Incoming seqs are placeholders — this ledger re-allocates them
        (it is the only seq authority).
        """
        events: list[LoopEvent] = []
        for entry in entries:
            payload = dict(entry.payload)
            event = self._emit("model", TYPE_COMPACTION, payload)
            lo = int(payload["replaces_from_seq"])
            hi = int(payload["replaces_to_seq"])
            summary = str(payload["summary"])
            for seq in range(lo, hi + 1):
                if seq in self._result_msg_index:
                    self._replacement[seq] = summary
            events.append(event)
        return events

    def close_turn(
        self,
        end_reason: EndReason,
        *,
        model: str,
        structured_output: Any = None,
        cost_usd: float | None = None,
    ) -> LoopEvent:
        """Turn closure — the billing chain's single data source; every
        termination path must emit exactly one of these."""
        self._fold_step_message()
        return self._emit(
            "model",
            TYPE_TURN_DONE,
            {
                "end_reason": end_reason.name,
                "model": model,
                "num_steps": self._steps,
                "structured_output": structured_output,
                "cost_usd": cost_usd,
            },
            usage=self._usage,
        )

    # ------------------------------------------------------------------
    # Read side (LedgerView)
    # ------------------------------------------------------------------

    def entries(self) -> Sequence[LedgerEntry]:
        return tuple(self._entries)

    def open_tool_calls(self) -> Sequence[ToolCall]:
        return tuple(self._open.values())

    def total_usage(self) -> Usage:
        return self._usage

    def last_input_tokens(self) -> int:
        return self._last_input_tokens

    def num_steps(self) -> int:
        return self._steps

    def replaced_seqs(self) -> frozenset[int]:
        return frozenset(self._replacement)

    def result_seq_sizes(self) -> list[tuple[int, int]]:
        """(seq, projected content length) per unreplaced tool result,
        oldest first — the compaction policy's shopping list."""
        sizes: list[tuple[int, int]] = []
        for seq, idx in self._result_msg_index.items():
            if seq in self._replacement:
                continue
            sizes.append((seq, len(str(self._turn_messages[idx].get("content", "")))))
        return sorted(sizes)

    def provider_messages(self) -> list[ProviderMessage]:
        """This turn's appended messages with compaction substitutions."""
        out: list[ProviderMessage] = []
        replaced_indexes = {
            self._result_msg_index[seq]: summary
            for seq, summary in self._replacement.items()
        }
        for idx, message in enumerate(self._turn_messages):
            if idx in replaced_indexes:
                message = {**message, "content": replaced_indexes[idx]}
            out.append(message)
        return out

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _fold_step_message(self) -> None:
        """Fold the current step's text + calls into one assistant message."""
        if not self._step_text and not self._step_calls:
            return
        message: ProviderMessage = {"role": "assistant"}
        text = "".join(self._step_text)
        message["content"] = text if text else None
        if self._step_calls:
            import json

            message["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.args, ensure_ascii=False),
                    },
                }
                for call in self._step_calls
            ]
        self._turn_messages.append(message)
        self._step_text = []
        self._step_calls = []

    def _emit(
        self,
        track: str,
        type_: str,
        payload: dict[str, Any],
        *,
        usage: Usage | None = None,
    ) -> LoopEvent:
        seq = self._seq
        self._seq += 1
        entry = LedgerEntry(seq=seq, track=track, type=type_, payload=payload, usage=usage)  # type: ignore[arg-type]
        self._entries.append(entry)
        return LoopEvent(track=track, seq=seq, type=type_, payload=payload, usage=usage)  # type: ignore[arg-type]
