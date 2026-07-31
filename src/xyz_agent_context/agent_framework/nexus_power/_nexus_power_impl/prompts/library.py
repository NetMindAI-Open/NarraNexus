"""
@file_name: library.py
@author: Bin Liang
@date: 2026-07-29
@description: NexusPowerPrompts — the single namespace for every prompt the
framework speaks (Owner decision 2026-07-29: one non-instantiable class;
call classmethods, get strings).

Design points:
  1. Non-instantiable: statelessness is enforced, not promised.
  2. The class LOADS AND FILLS only; long-form copy lives in
     ``resources/*.md`` — wording changes never touch code review.
  3. Pure functions: no clocks, no environment, no randomness; the
     byte-stability CI test aims straight at this class.
  4. Overridable: a subclass replacing individual classmethods is a
     complete prompt pack; the assembler takes the class reference.

Section roster (v1): the constitution (the monologue/expression
contract — the framework's identity), workspace tool guidance, and the
expandable-capability frame + cards. The platform's own system prompt
(identity, scenario, modules) arrives inside the materialized messages
and is deliberately NOT restated here.
"""

from __future__ import annotations

from functools import lru_cache
from importlib import resources as importlib_resources
from typing import Callable

from xyz_agent_context.agent_framework.nexus_power._nexus_power_impl.prompts.assembler import (
    PromptInputs,
    PromptMode,
)

_SectionFn = Callable[[PromptInputs, PromptMode], str]
_RESOURCE_PACKAGE = f"{__package__}.resources"


@lru_cache(maxsize=None)
def _load(resource_name: str) -> str:
    """Read a template from resources/ (missing template = build error)."""
    path = importlib_resources.files(_RESOURCE_PACKAGE).joinpath(resource_name)
    return path.read_text(encoding="utf-8").strip()


class NexusPowerPrompts:
    """Prompt namespace — never instantiate; subclass to make a pack."""

    def __init__(self) -> None:
        raise TypeError(
            "NexusPowerPrompts is a namespace: call classmethods, subclass for packs"
        )

    # ---- stable prefix (S layer: byte-stable within a session) -------

    @classmethod
    def constitution(cls, inputs: PromptInputs, mode: PromptMode) -> str:
        """S1 — the monologue/expression contract. Non-empty in every
        mode: this is the framework's minimum identity.

        The reply-tool example is per-turn DATA (the platform's declared
        default), never a platform tool name baked into framework copy:
        the framework knows no platform, and some turns are legitimately
        mute (no example to give)."""
        if mode is PromptMode.NONE:
            return (
                "Your plain text is private monologue; only tool calls act "
                "on the world."
            )
        example = (
            f" (this turn's default: `{inputs.default_reply_tool}`)"
            if inputs.default_reply_tool
            else ""
        )
        return _load("constitution.md").replace(
            "{{DEFAULT_REPLY_TOOL_EXAMPLE}}", example
        )

    @classmethod
    def identity_line(cls, inputs: PromptInputs, mode: PromptMode) -> str:
        """S3 hook — optional extra identity from the caller (plain data;
        the platform's real identity prompt travels in the materialized
        messages)."""
        if mode is PromptMode.NONE:
            return ""
        return inputs.identity.strip()

    @classmethod
    def workspace_tools(cls, inputs: PromptInputs, mode: PromptMode) -> str:
        """S5 — guidance for the builtin surface, present only when the
        file/shell groups are actually mounted (what the model reads is
        derived from what is registered — no drift)."""
        if mode is PromptMode.NONE:
            return ""
        if "files" not in inputs.builtin_groups and "shell" not in inputs.builtin_groups:
            return ""
        return _load("builtin_tools.md")

    # ---- dynamic tail (V layer: per turn, append-only) ---------------

    @classmethod
    def capability_frame(cls, inputs: PromptInputs, mode: PromptMode) -> str:
        """V — the expandable-capability frame + CARD index. Dynamic
        because the catalog is per-turn; discovery is never trimmed
        (cards always ride along when a catalog exists)."""
        if mode is not PromptMode.FULL or not inputs.capability_cards:
            return ""
        return _load("capability_expansion.md") + "\n\n" + inputs.capability_cards

    @classmethod
    def expanded_instructions(cls, inputs: PromptInputs, mode: PromptMode) -> str:
        """V — instructions from initial expansions (start-of-turn
        expansion joins the prompt; mid-turn expansion returns through
        the tool result instead)."""
        if not inputs.capability_instructions:
            return ""
        return inputs.capability_instructions

    # ---- out-of-band copy (not a section: the assembly renders these
    # at its own placement points) -------------------------------------

    @classmethod
    def reply_reminder(cls, reply_tools: tuple[str, ...]) -> str:
        """The dynamic-tail delivery reminder, rendered fresh each step
        from the expression contract's CURRENT tool list (expansion may
        grow it mid-turn — the tail is the one placement where that is
        cache-free). Empty list = mute turn = no reminder."""
        if not reply_tools:
            return ""
        names = ", ".join(f"`{name}`" for name in reply_tools)
        return _load("reply_reminder.md").replace("{{REPLY_TOOLS}}", names)

    # ---- section rosters (order is contract: reordering breaks every
    # user's cache prefix and requires explicit review) ----------------

    @classmethod
    def plan_block(cls, inputs: PromptInputs, mode: PromptMode) -> str:
        """V — the live plan, re-injected on EVERY step. Compaction can
        eat history but never the prompt, so this is the one placement
        where a long task's plan survives."""
        return inputs.plan_block

    @classmethod
    def stable_sections(cls) -> tuple[_SectionFn, ...]:
        return (cls.constitution, cls.identity_line, cls.workspace_tools)

    @classmethod
    def dynamic_sections(cls) -> tuple[_SectionFn, ...]:
        return (cls.capability_frame, cls.expanded_instructions, cls.plan_block)
