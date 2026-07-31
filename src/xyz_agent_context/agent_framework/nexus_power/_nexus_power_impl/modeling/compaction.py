"""
@file_name: compaction.py
@author: Bin Liang
@date: 2026-07-29
@description: CompactionPolicy implementations — compaction ships in v1
(a long-running turn must never die on the context wall; claude-code's
internal auto-compact means shipping without one would be a regression).

Two tiers behind one protocol (an assembly swap upgrades):
  - ``ToolResultPruner`` (v1 default): deterministic pruning, no LLM —
    stale tool results collapse to a one-line placeholder. Zero cost,
    zero hallucination risk.
  - ``SummaryCompactor`` (v1.5 seat): auxiliary-model summarization of
    the middle window. Billing decision (Owner 2026-07-29): the user's
    own model by default, cost itemized separately.

Narrative linkage happens THROUGH the event log, keeping the loop
decoupled: compaction entries stream out like any event; the platform's
memory service consumes them into long-term memory ("compaction moves
information, it does not destroy it").

Triggering is dual: proactive (``should_compact`` against the window
threshold) and reactive (the loop calls ``compact`` directly on a
``CONTEXT_OVERFLOW`` classification, then retries the step).
"""

from __future__ import annotations

from typing import Sequence

from xyz_agent_context.agent_framework.nexus_power.contracts.events import (
    TYPE_COMPACTION,
    LedgerEntry,
)
from xyz_agent_context.agent_framework.nexus_power.contracts.model import ProviderProfile
from xyz_agent_context.agent_framework.nexus_power.contracts.protocols import LedgerView

# Rough chars-per-token for SAVINGS ESTIMATION ONLY (billing always uses
# provider-reported usage; constraint C3 is untouched).
_CHARS_PER_TOKEN = 4


def estimate_message_tokens(messages: Sequence[dict]) -> int:
    """Rough size of a projected conversation, same ratio as above.

    Sizing only, and deliberately shared with the compaction estimate:
    the output clamp needs a number on the FIRST step of a turn, where
    the ledger has no measured input yet but the projection already
    carries every earlier turn.

    The WHOLE message is measured, not just ``content``. A tool-only
    assistant step puts its payload in the sibling ``tool_calls`` key and
    sets ``content`` to None, so sizing off content alone valued a 16KB
    write_file at one token. Serializing the dict over-counts slightly
    (keys, quoting) — the safe direction for a clamp, since
    under-counting is what lets a request sail past the wall.
    """
    return sum(len(str(m)) for m in messages) // _CHARS_PER_TOKEN


class ToolResultPruner:
    """Deterministic tool-result pruning (v1 default).

    Pairing safety by construction: a placeholder is still a legal tool
    message — pairs are replaced, never split. The recent tail is
    protected: the newest ``keep_recent_results`` results never prune.
    """

    def __init__(
        self,
        *,
        trigger_ratio: float = 0.75,
        keep_recent_results: int = 4,
        min_prunable_chars: int = 512,
    ) -> None:
        self._trigger_ratio = trigger_ratio
        self._keep_recent = keep_recent_results
        self._min_chars = min_prunable_chars

    def should_compact(self, ledger: LedgerView, profile: ProviderProfile) -> bool:
        last_input = ledger.last_input_tokens()
        if not last_input:
            return False
        return last_input >= profile.context_window * self._trigger_ratio

    async def compact(
        self, ledger: LedgerView, profile: ProviderProfile
    ) -> Sequence[LedgerEntry]:
        """Prune oldest-first until the estimated savings reach half the
        overflow margin, or candidates run out."""
        sizes = list(getattr(ledger, "result_seq_sizes")())
        if len(sizes) <= self._keep_recent:
            return ()
        candidates = sizes[: len(sizes) - self._keep_recent]
        target_chars = self._target_savings_chars(ledger, profile)
        entries: list[LedgerEntry] = []
        saved = 0
        for seq, size in candidates:
            if size < self._min_chars:
                continue
            summary = f"[pruned] earlier tool result ({size} chars) elided to fit context"
            entries.append(
                LedgerEntry(
                    seq=-1,  # placeholder; the ledger re-allocates
                    track="model",
                    type=TYPE_COMPACTION,
                    payload={
                        "replaces_from_seq": seq,
                        "replaces_to_seq": seq,
                        "summary": summary,
                        "retained_tail_seq": sizes[-self._keep_recent][0],
                    },
                )
            )
            saved += max(0, size - len(summary))
            if target_chars and saved >= target_chars:
                break
        return tuple(entries)

    def _target_savings_chars(self, ledger: LedgerView, profile: ProviderProfile) -> int:
        overflow_tokens = max(
            0, ledger.last_input_tokens() - int(profile.context_window * 0.5)
        )
        return overflow_tokens * _CHARS_PER_TOKEN


class SummaryCompactor:
    """v1.5 seat: auxiliary-model summarization (cascades after pruning).

    Billing (Owner 2026-07-29): defaults to the agent's own configured
    model — the user's model, the user's key, the user's cost, itemized
    as a separate ``compaction`` usage line. ``summary_model`` remains a
    user-facing override; the platform never picks silently.
    """

    def __init__(self, *, summary_model: str | None = None) -> None:
        self._summary_model = summary_model

    def should_compact(self, ledger: LedgerView, profile: ProviderProfile) -> bool:
        return False  # v1: never active; assembly mounts the pruner.

    async def compact(
        self, ledger: LedgerView, profile: ProviderProfile
    ) -> Sequence[LedgerEntry]:
        raise NotImplementedError(
            "SummaryCompactor ships in v1.5; assemble ToolResultPruner instead"
        )
