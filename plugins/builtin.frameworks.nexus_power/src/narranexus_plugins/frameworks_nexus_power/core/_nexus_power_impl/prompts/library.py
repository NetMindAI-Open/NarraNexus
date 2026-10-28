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

from narranexus_plugins.frameworks_nexus_power.core._nexus_power_impl.prompts.assembler import (
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

    # Rule 1 owns every "do my words reach anyone" claim, because it is the
    # only rule that can know: `default_reply_tool` empty is the framework's
    # existing signal for a MUTE turn, where the platform has withheld every
    # expressive declaration because plain text is itself the delivered
    # artifact (the platform's `is_plain_text_turn`). Rules 2/3/4 therefore
    # state action and turn-end semantics only — if a delivery claim lived in
    # one of them it would be unconditional, and on a mute turn the model
    # would read rule 1 saying "what you write is the output" against rule 2
    # saying "plain text reaches no one". The louder rule wins, and the safest
    # reading of rule 2 is to write nothing — which on a patrol turn means the
    # status line silently stops appearing, with no error and no failing test.
    #
    # The copy itself stays in resources/ (this class loads and fills, never
    # holds prose — see the file header), so wording changes never go through
    # Python review. The framework names no scenario: it reads one bit of
    # data, "is there an expressive tool" (iron rule #4).

    @classmethod
    def constitution(cls, inputs: PromptInputs, mode: PromptMode) -> str:
        """S1 — the monologue/expression contract. Non-empty in every
        mode: this is the framework's minimum identity.

        The reply-tool example is per-turn DATA (the platform's declared
        default), never a platform tool name baked into framework copy:
        the framework knows no platform, and some turns are legitimately
        mute (no example to give). A mute turn also swaps rule 1's whole
        delivery passage — see the note above."""
        speaks_by_tool = bool(inputs.default_reply_tool)
        if mode is PromptMode.NONE:
            if not speaks_by_tool:
                # Kept under the NONE face's size budget (see the mode-faces
                # test) while still carrying BOTH rules the full face has:
                # how this turn delivers, and that everything else is tools.
                return (
                    "Your plain text IS this turn's output — you have no "
                    "reply tool. Write only the finished thing, with no "
                    "narration of what you are about to do. Everything else "
                    "happens only through tool calls."
                )
            return (
                "Your plain text is visible working narration, never a "
                "delivered message; only tool calls act on the world. Before "
                "each tool call, say in one short sentence what you are about "
                "to do."
            )
        example = (
            f" (this turn's default: `{inputs.default_reply_tool}`)"
            if speaks_by_tool
            else ""
        )
        delivery = _load(
            "delivery_speaks_by_tool.md" if speaks_by_tool
            else "delivery_speaks_by_writing.md"
        )
        return (
            _load("constitution.md")
            .replace("{{DEFAULT_REPLY_TOOL_EXAMPLE}}", example)
            # `_load` strips, and the slot sits BETWEEN two paragraphs of
            # rule 1. Restore both things the strip removed: the blank lines
            # that keep it a separate paragraph, and the FIRST line's 3-space
            # indent (every later line still carries its own — the indent
            # aligns the passage under the numbered list item).
            .replace("{{PLAIN_TEXT_DELIVERY}}", f"\n   {delivery}\n")
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
        cache-free). Empty list = mute turn = no reminder.

        The declared order is contract (the first name is the turn's
        default reply tool — ExpressionContract) and the reminder SAYS
        so instead of flattening the list: 2026-08-13 voice call showed
        models following a flat list over the per-message instruction on
        12/14 turns."""
        if not reply_tools:
            return ""
        default = f"`{reply_tools[0]}`"
        others = ", ".join(f"`{name}`" for name in reply_tools[1:])
        clause = f" (other reply tools, only when the situation clearly calls for them: {others})" if others else ""
        return (
            _load("reply_reminder.md")
            .replace("{{DEFAULT_REPLY_TOOL}}", default)
            .replace("{{OTHER_REPLY_TOOLS_CLAUSE}}", clause)
        )

    @classmethod
    def expression_nudge(cls, reply_tools: tuple[str, ...]) -> str:
        """The one-shot mute-turn repair message (loop STOP_CHECK,
        opt-in via ``expression_nudge``). Names the turn's default reply
        tool so the model's next step has exactly one obvious move."""
        default = f"`{reply_tools[0]}`" if reply_tools else "a reply tool"
        return (
            "You are ending this turn WITHOUT having called any reply "
            "tool — the user will receive nothing but silence. That is "
            "never the right outcome for a direct conversation. Call "
            f"{default} now with your reply; if the incoming message was "
            "unclear or garbled, reply with ONE short clarifying "
            "question instead."
        )

    @classmethod
    def wait_timed_out(cls, seconds: int) -> str:
        """The timeout message injected when a ``wait_for_input`` wait elapses with
        nothing arriving (loop WAIT boundary). Tells the agent the wait is over
        so it wraps up rather than assuming it is still waiting — and names the
        alternative (wait again) without pushing it, so a genuinely-idle turn
        closes instead of spinning."""
        return (
            f"You waited {seconds}s and no new message arrived. Wrap up this "
            "turn now — end with a brief reply if the situation calls for one. "
            "Only call `wait_for_input` again if a reply is still genuinely "
            "expected; do not wait repeatedly with nothing happening."
        )

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
