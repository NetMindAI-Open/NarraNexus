"""
@file_name: assembler.py
@author: Bin Liang
@date: 2026-07-29
@description: Prompt assembly — pure-function sections, three modes, an
explicit stable-prefix / dynamic-tail split (constraint C2).

Assembly disciplines (synthesized from Codex / Hermes / OpenClaw source
surveys): every section is a pure classmethod on the prompts namespace
class; empty sections vanish; the assembled bytes are a deterministic
function of the inputs (no clocks, no environment, no randomness) —
byte-stability is CI-tested. ``PromptAssembler`` consumes a CLASS
reference, so a subclass of ``NexusPowerPrompts`` is a complete prompt pack
(experiments / A-B / per-scenario voices) with zero assembler changes.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - import cycle guard for typing only
    from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.prompts.library import (
        NexusPowerPrompts,
    )


class PromptMode(Enum):
    """Prompt trim levels (one prompt, derived faces — never two copies)."""

    FULL = "full"        # main agent
    MINIMAL = "minimal"  # subagents: constitution + tools only
    NONE = "none"        # bare identity line (judge/evaluator scenarios)


@dataclass(frozen=True)
class PromptInputs:
    """Everything the sections may draw on — injected data only.

    The framework knows nothing about where these come from (Awareness,
    modules, narrative — all platform concepts end here as plain data).
    """

    builtin_groups: tuple[str, ...] = ()
    capability_cards: str = ""            # CARD index text (key: card per line)
    capability_instructions: str = ""     # initial expansions' instructions
    identity: str = ""                    # optional extra identity line
    plan_block: str = ""                  # current plan, re-injected every step
    # The turn's default reply tool (the first platform-declared
    # expressive tool). Rendered into the constitution's example slot;
    # frozen at assembly so the stable prefix never moves mid-turn.
    default_reply_tool: str = ""


@dataclass(frozen=True)
class AssembledPrompt:
    """The C2 split made structural: prefix must stay byte-stable across
    a session; the tail may vary per turn and only ever appends."""

    stable_prefix: str
    dynamic_tail: str

    def messages(self) -> list[dict[str, str]]:
        """As system messages (empty parts vanish)."""
        out = []
        if self.stable_prefix:
            out.append({"role": "system", "content": self.stable_prefix})
        if self.dynamic_tail:
            out.append({"role": "system", "content": self.dynamic_tail})
        return out


_SECTION_JOINER = "\n\n"


class PromptAssembler:
    """Orders and joins sections from a prompts namespace class."""

    def __init__(self, prompts_cls: type["NexusPowerPrompts"] | None = None) -> None:
        if prompts_cls is None:
            from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.prompts.library import (
                NexusPowerPrompts,
            )

            prompts_cls = NexusPowerPrompts
        self._prompts = prompts_cls

    def assemble(self, inputs: PromptInputs, mode: PromptMode) -> AssembledPrompt:
        """Deterministic assembly: same inputs → identical bytes."""
        stable = [
            section(inputs, mode) for section in self._prompts.stable_sections()
        ]
        dynamic = [
            section(inputs, mode) for section in self._prompts.dynamic_sections()
        ]
        return AssembledPrompt(
            stable_prefix=_SECTION_JOINER.join(s for s in stable if s),
            dynamic_tail=_SECTION_JOINER.join(s for s in dynamic if s),
        )
