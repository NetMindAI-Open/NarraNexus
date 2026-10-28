"""
@file_name: turn_profile.py
@date: 2026-08-06
@description: Per-turn execution profile (fast mode). None everywhere = today's behavior.

The profile rides the existing pure-kwargs chain (run_stream ->
AgentRuntime.run -> RunContext -> TurnInput -> driver kwargs -> executor
wire) as one frozen value object. Every consumer must treat an absent
profile — and a profile left at defaults — as "behave exactly as today";
fast mode is strictly additive.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel


class TurnProfile(BaseModel, frozen=True):
    """Per-turn knobs for fast mode.

    Every field's default preserves the current pipeline behavior; a
    consumer receiving ``profile=None`` must not change anything.
    JSON-serializable (``model_dump()`` crosses the executor wire).
    """

    name: str = "default"
    narrative_strategy: Literal["full", "bm25_top1"] = "full"
    framework_override: Optional[str] = None          # e.g. "nexus_power"
    prompt_mode: Literal["full", "minimal"] = "full"
    reasoning_effort: Optional[str] = None            # -> llm_extra["reasoning_effort"]
    include_arg_deltas: Optional[bool] = None         # None = TurnOptions default
    expression_nudge: Optional[bool] = None           # None = TurnOptions default
    # Consulted only by the bm25_top1 fast path. "ephemeral" is the F28
    # voice contract: a miss runs the turn bare, no creation, no session
    # writes. "durable" is for persisted chat surfaces: a miss creates the
    # narrative (CRUD, no LLM) and the session continuity anchor is kept
    # consistent — a fast turn must never vanish from history.
    narrative_persistence: Literal["ephemeral", "durable"] = "ephemeral"
    # NOTE deliberately absent: a reply_tool field. The reply surface is
    # declared by modules (expressive_tools orders speak first on
    # voice turns via extra_data) — a profile field nothing consumes
    # would be exactly the declared-but-unimplemented schema trap
    # turn_input.py warns about.

    @classmethod
    def fast_for(
        cls, working_source: object, *, reasoning_effort: str = "low"
    ) -> "TurnProfile":
        """Build the fast profile for a trigger surface.

        The single source of truth for what "fast" means. ``working_source``
        is a ``WorkingSource`` enum member or its bare string value; the
        profile name derives from it (``"<source>_fast"``) so [turn-timing]
        logs separate surfaces without per-surface factories. Knobs carry
        the shared F28 v1 decisions: FULL prompt, tools kept, BM25 top-1
        narrative, low reasoning effort.
        """
        from narranexus.platform.schema.hook_schema import WorkingSource

        source = getattr(working_source, "value", None) or str(working_source)
        # The one per-surface knob, and its whitelist is WorkingSource.
        # is_from_human — the same judgement the step layer uses to honor
        # durability, so declaration and enforcement can never diverge:
        # persisted human chat surfaces (chat/lark/slack/telegram) are
        # durable (miss = create, anchor kept); background/machine sources
        # (job/message_bus/callback) and non-enum surfaces (the live
        # "voice" RTC path) stay ephemeral (miss = bare turn).
        try:
            durable = WorkingSource(source).is_from_human()
        except ValueError:
            durable = False
        return cls(
            name=f"{source}_fast",
            narrative_persistence="durable" if durable else "ephemeral",
            narrative_strategy="bm25_top1",
            framework_override="nexus_power",
            prompt_mode="full",
            reasoning_effort=reasoning_effort,
            include_arg_deltas=True,
            # An unanswered fast turn is never the right outcome — a
            # mute turn gets one steering nudge before it may close.
            expression_nudge=True,
        )

    @classmethod
    def voice_fast(cls, *, reasoning_effort: str = "low") -> "TurnProfile":
        """The F28 voice-call profile — ``fast_for("voice")`` by another name."""
        return cls.fast_for("voice", reasoning_effort=reasoning_effort)

    @property
    def is_fast(self) -> bool:
        return self.name != "default"
