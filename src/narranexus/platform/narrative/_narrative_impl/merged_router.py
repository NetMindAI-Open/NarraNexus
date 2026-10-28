"""
@file_name: merged_router.py
@date: 2026-08-26
@description: One helper call that answers both routing questions — "does this
message continue the thread?" and "if not, where does it go?".

WHY (reference/self_notebook/specs/2026-08-25-merged-routing-design.md §2-§4)

The two questions were two serial LLM calls. On prod (7 days, is_user_chat=1,
n=189) 43 turns paid for both, at a serial p50 of 8,924ms; the entire non-LLM
half of routing is 47.6ms mean. So the only lever with anything behind it is the
NUMBER OF ROUND TRIPS, and the two questions share almost all of their input.

WHAT THIS FILE IS RESPONSIBLE FOR, AND WHAT IT IS NOT

It builds one prompt and parses one answer. It does not decide where a turn
lands: `merged_select.select_merged` owns that, and every landing it uses is
a pre-existing executor (the continuity landing, the judge's match landing,
`create_from_query`, `_land_no_topic_turn`). The decider changes; the executors
do not.

THE STRUCTURAL CONSTRAINT THAT SHAPES THE PROMPT (§3.2)

On the two-call path, a continuity turn returns BEFORE the retrieval tier: no
pool, no menu, no way for a foreign thread to reach any prompt. Merging opens
that door on every turn — and the measurement says what is behind it. On
continuity turns the anchored thread is NOT in the BM25 top-3 in 26.2%-71.6% of
turns, the menu's first row is a foreign thread in 26.4%-93.8% (78-97% of those
being seeded distractor threads), and the anchor scores literally zero in
8.2%-49.3%. The p07 hijack specimen was held by exactly the defence merging
removes: `pool=0`, BM25 never ran.

Hence: the anchor is rendered UNCONDITIONALLY, in its own section, as the
default answer, and deduplicated out of the menu. Its presence is not a
function of its score. That is data-forced, not stylistic — and
`test_merged_routing_prompt.py` fails if it ever becomes conditional again.

FAILURE IS NOT AN ANSWER

`decide` never raises and never guesses. A provider error, a timeout, a verdict
outside the contract or an index past the menu all return `ok=False`, and the
caller's fallback keeps the turn where it already was. Reading a failed call as
"new topic" is the D19 shape: the created thread becomes the anchor and the
updater rewrites it until the lexical evidence agrees.
"""

from __future__ import annotations

import time as _perf
from dataclasses import dataclass, field
from typing import FrozenSet, List, Optional, Tuple, TYPE_CHECKING

from loguru import logger
from pydantic import BaseModel, Field

from narranexus.platform.agent_framework.llm.helper_sdk import get_helper_sdk

from ..config import config
from . import routing_blocks
from .prompts_merged import build_merged_instructions

if TYPE_CHECKING:
    from ..models import Narrative


# ── the contract ───────────────────────────────────────────────────────────

#: The message belongs to the thread the conversation is already on. Kept
#: SEPARATE from `match` even when the anchor is also a menu row: the judge's
#: `search` exit cannot tell "confirmed the continuation" from "happened to pick
#: the anchor", and only the first of those must not be audited as a switch.
VERDICT_CONTINUE_ANCHOR = "continue_anchor"
#: A different existing thread from the keyword menu, by index.
VERDICT_MATCH = "match"
#: A thread the user was invited into, by index. Structural, exactly as today.
VERDICT_PARTICIPANT = "participant"
#: A real subject nothing on offer covers.
VERDICT_NEW = "new"
#: Nothing worth remembering as its own thread — the verdict, not a destination.
VERDICT_NO_TOPIC = "no_topic"

VALID_VERDICTS = frozenset(
    {
        VERDICT_CONTINUE_ANCHOR,
        VERDICT_MATCH,
        VERDICT_PARTICIPANT,
        VERDICT_NEW,
        VERDICT_NO_TOPIC,
    }
)

#: Written to `narrative_routing_audit.merged_verdict` when the call produced
#: nothing usable. A distinct value, not an empty string: "the model was asked
#: and could not be used" and "no model was asked" are different rows, and only
#: one of them is a provider problem.
VERDICT_FAILED = "failed"


class MergedRoutingOutput(BaseModel):
    """What the helper LLM returns. Mirrors `UnifiedMatchOutput`'s shape.

    One shared `match_index` across the verdicts that name a candidate, as the
    judge already does — two index fields would let a model fill the wrong one.
    """

    reason: str = Field(default="", description="Reasoning for the verdict")
    verdict: str = Field(
        default="",
        description="one of the verdicts offered in the instructions",
    )
    match_index: int = Field(default=-1, description="0-based index, -1 when none")


# ── prompt input ───────────────────────────────────────────────────────────

ANCHOR_ABSENT_NOTE = (
    "\nThe anchored thread: none — this conversation is not currently on any "
    "thread, so there is nothing to continue.\n"
)
ANCHOR_NOT_CONTINUABLE_NOTE = (
    "\nThe anchored thread: the previous turn was filed into a legacy container "
    "rather than a real thread, so it cannot be continued. Route this message "
    "on its own merits.\n"
)
NO_PREVIOUS_TURN_NOTE = (
    "Previous conversation turn: none — this is the first message of the "
    "conversation."
)
#: `## `-prefixed like the menu and awareness sections, so the model sees one
#: consistent section grammar. The previous-turn block keeps its own inherited
#: self-label instead of gaining a header — it is shared verbatim with the
#: continuity tier, and that byte-identity is worth more than the symmetry.
_MERGED_ANCHOR_HEADER = (
    "## The thread you are already on — staying here is the DEFAULT answer:"
)
_MERGED_MENU_HEADER = "## Other candidate threads (needed only to switch):"
_MERGED_MENU_EMPTY_NOTE = (
    "(none — no other thread of this user shares any wording with this "
    "message)\n\n"
)


@dataclass(frozen=True)
class MergedRoutingInput:
    """Everything the merged call reads, in priority order.

    ``anchor_is_continuable`` is `narrative_service.is_reusable_anchor` applied
    by the caller — THE one definition, consumed here rather than re-derived, so
    the merged path cannot disagree with the fast path and the no-topic landing
    about what counts as a thread.
    """

    query: str
    anchor: Optional["Narrative"]
    anchor_is_continuable: bool
    previous_query: str
    previous_response: str
    minutes_since_previous: Optional[float]
    #: Judge-shaped candidate dicts (see `landings.candidate_labels`), already
    #: deduplicated against the anchor and the participants by `pick_menu`.
    menu: List[dict] = field(default_factory=list)
    participants: List[dict] = field(default_factory=list)
    awareness: Optional[str] = None


@dataclass(frozen=True)
class MergedRoutingPrompt:
    instructions: str
    user_input: str
    #: Section codes whose content hit its cap, for `merged_truncated`.
    truncated: Tuple[str, ...]

    @property
    def input_chars(self) -> int:
        """Every character this call sends — instructions AND user input.

        The instructions vary by variant (answer table 2-5 entries, priority
        list 2-4 lines, participant preamble present or not), and the variant
        correlates with the turn shape being measured — omitting them would
        bias the latency slope's x axis, not just offset it (review round 2,
        I2). `merged_truncated` covers only the user-input side: instructions
        are not subject to the read-side budgets.
        """
        return len(self.instructions) + len(self.user_input)


@dataclass(frozen=True)
class MergedRoutingDecision:
    """One merged verdict, or the honest absence of one."""

    ok: bool
    verdict: str
    reason: str
    match_index: int
    elapsed_ms: int
    prompt: Optional[MergedRoutingPrompt] = None


def build_merged_prompt(inp: MergedRoutingInput) -> MergedRoutingPrompt:
    """Assemble the one prompt. Order in the body IS priority order.

    Sections, and why they sit where they do:
      1. the previous turn — the continuity tier's exclusive input, and the only
         thing that can route a zero-overlap follow-up ("那第二步呢");
      2. the anchored thread — unconditional, see §3.2 above;
      3. the participant section (variant only) — P0-4 priority, structural;
      4. the keyword menu — evidence for LEAVING, deduplicated;
      5. awareness — the agent's standing domain;
      6. this turn's message.
    """
    truncated: List[str] = []

    previous = routing_blocks.render_previous_turn(
        inp.previous_query,
        inp.previous_response,
        query_max_chars=config.MERGED_QUERY_MAX_CHARS,
        response_max_chars=config.MERGED_PREV_RESPONSE_MAX_CHARS,
        absent_note=NO_PREVIOUS_TURN_NOTE,
    )
    truncated.extend(previous.truncated)

    if inp.anchor is None:
        anchor_block = routing_blocks.Rendered(ANCHOR_ABSENT_NOTE)
    elif not inp.anchor_is_continuable:
        # Shown, not offered: the model must know where the previous turn was
        # filed, and C-1 says a legacy container is a verdict about that turn
        # rather than a thread anyone may continue.
        anchor_block = routing_blocks.Rendered(
            ANCHOR_NOT_CONTINUABLE_NOTE
            + routing_blocks.render_anchor_context(
                inp.anchor,
                header="## Where the previous turn was filed:",
                summary_max_chars=config.MERGED_ANCHOR_SUMMARY_MAX_CHARS,
                include_bucket_note=False,
            ).text
        )
    else:
        anchor_block = routing_blocks.render_anchor_context(
            inp.anchor,
            header=_MERGED_ANCHOR_HEADER,
            summary_max_chars=config.MERGED_ANCHOR_SUMMARY_MAX_CHARS,
            include_bucket_note=False,
        )
    truncated.extend(anchor_block.truncated)

    # No renderer-side cap (round 6, minor 2): inp.participants IS what the
    # contract bounds on, so the entry truncation in merged_select is the one
    # cut — re-capping here would let a different MergedRoutingInput
    # constructor reopen the ballot/render split (round 2, I1).
    participants = routing_blocks.render_participant_candidates(
        inp.participants,
    )
    truncated.extend(participants.truncated)

    menu = routing_blocks.render_search_candidates(
        inp.menu,
        header=_MERGED_MENU_HEADER,
        empty_note=_MERGED_MENU_EMPTY_NOTE,
        # The squashed score is `raw/(raw+1)` over an IDF table computed on this
        # pool alone: it means nothing the model can use, and showing it invites
        # exactly the "0.91 must be a match" reading the judge's own evidence
        # lines were added to prevent.
        include_score=False,
    )
    truncated.extend(menu.truncated)

    awareness_text, awareness_clipped = routing_blocks.clamp_head(
        inp.awareness or "", config.MERGED_AWARENESS_MAX_CHARS
    )
    if awareness_clipped:
        truncated.append("awareness")
    awareness_block = (
        f"\n## Agent Awareness\n{awareness_text}\n" if awareness_text else ""
    )

    query_text, query_clipped = routing_blocks.clamp_head(
        inp.query, config.MERGED_QUERY_MAX_CHARS
    )
    if query_clipped:
        truncated.append("query")

    elapsed = (
        f"\nTime elapsed since that turn: {inp.minutes_since_previous:.1f} minutes\n"
        if inp.minutes_since_previous is not None
        else ""
    )

    user_input = (
        f"{previous.text}\n"
        f"{elapsed}"
        f"{anchor_block.text}\n"
        f"{participants.text}"
        f"{menu.text}"
        f"{awareness_block}"
        f"\n## The user's current message\n{query_text}\n"
    )

    instructions = build_merged_instructions(
        anchor_is_continuable=bool(inp.anchor is not None and inp.anchor_is_continuable),
        with_participants=bool(inp.participants),
        with_menu=bool(inp.menu),
    )
    return MergedRoutingPrompt(
        instructions=instructions,
        user_input=user_input,
        truncated=tuple(dict.fromkeys(truncated)),
    )


async def decide(inp: MergedRoutingInput) -> MergedRoutingDecision:
    """Ask once. Never raise, never guess.

    Validation is part of the answer, not a courtesy: an index past the menu or
    a verdict outside the contract is indistinguishable, downstream, from a
    confident wrong routing — so both come back as `ok=False` and the caller
    keeps the turn where it already was.
    """
    try:
        prompt = build_merged_prompt(inp)
    except Exception as e:  # noqa: BLE001 — an assembly failure is not a verdict
        # Rule 6 covers the WHOLE decider, assembly included (review round 5,
        # I2): un-caught, a bad candidate field would kill the user's turn
        # here, while the same defect on the two-call path is one warning and
        # a degrade. Distinct log text from the call failure below — during
        # the grey period, "our prompt bug" and "provider problem" must be
        # tellable apart on the merged_verdict='failed' rows.
        logger.warning(
            f"[MergedRouting] prompt build failed: {type(e).__name__}: {e} "
            f"(the turn stays where it was)"
        )
        return MergedRoutingDecision(
            ok=False, verdict=VERDICT_FAILED,
            reason=f"prompt build failed: {e}",
            match_index=-1, elapsed_ms=0, prompt=None,
        )
    # Timing starts AFTER assembly, before the await: merged_ms is the CALL's
    # self time (models.py), and folding assembly in would tilt the
    # input_chars -> merged_ms regression this column exists to feed.
    started = _perf.monotonic()
    try:
        result = await get_helper_sdk().llm_function(
            instructions=prompt.instructions,
            user_input=prompt.user_input,
            output_type=MergedRoutingOutput,
            model=config.NARRATIVE_JUDGE_LLM_MODEL,
            reasoning_effort=config.NARRATIVE_JUDGE_LLM_REASONING_EFFORT or None,
        )
        output: MergedRoutingOutput = result.final_output
    except Exception as e:  # noqa: BLE001 — a provider failure is not a verdict
        elapsed_ms = int((_perf.monotonic() - started) * 1000)
        logger.warning(
            f"[MergedRouting] call failed after {elapsed_ms}ms: "
            f"{type(e).__name__}: {e} (the turn stays where it was)"
        )
        return MergedRoutingDecision(
            ok=False, verdict=VERDICT_FAILED, reason=f"merged call failed: {e}",
            match_index=-1, elapsed_ms=elapsed_ms, prompt=prompt,
        )

    elapsed_ms = int((_perf.monotonic() - started) * 1000)
    invalid = _contract_violation(output, inp)
    if invalid:
        logger.warning(
            f"[MergedRouting] off-contract answer ({invalid}): "
            f"verdict={output.verdict!r} index={output.match_index} "
            f"(the turn stays where it was)"
        )
        return MergedRoutingDecision(
            ok=False, verdict=VERDICT_FAILED,
            reason=f"off-contract answer ({invalid}): {output.reason}",
            match_index=-1, elapsed_ms=elapsed_ms, prompt=prompt,
        )

    logger.info(
        f"[MergedRouting] {output.verdict}"
        f"{f' #{output.match_index}' if output.match_index >= 0 else ''} — "
        f"{output.reason[:120]}"
    )
    return MergedRoutingDecision(
        ok=True, verdict=output.verdict, reason=output.reason,
        match_index=output.match_index, elapsed_ms=elapsed_ms, prompt=prompt,
    )


def allowed_verdicts(inp: MergedRoutingInput) -> FrozenSet[str]:
    """The verdicts THIS turn actually offers — one derivation.

    `build_merged_instructions` renders exactly these into the answer table,
    and `_contract_violation` refuses anything outside them, so the prose and
    the contract cannot disagree (review Critical 1: they used to — the shared
    core offered continue_anchor on turns the contract was guaranteed to
    refuse, and every obedient model landed in merged_fallback_new).
    """
    offered = {VERDICT_NEW, VERDICT_NO_TOPIC}
    if inp.menu:
        offered.add(VERDICT_MATCH)
    if inp.anchor is not None and inp.anchor_is_continuable:
        offered.add(VERDICT_CONTINUE_ANCHOR)
    if inp.participants:
        offered.add(VERDICT_PARTICIPANT)
    return frozenset(offered)


def _contract_violation(
    output: MergedRoutingOutput, inp: MergedRoutingInput
) -> Optional[str]:
    """Why this answer is unusable, or None if it is fine."""
    if output.verdict not in VALID_VERDICTS:
        return "unknown verdict"
    if output.verdict not in allowed_verdicts(inp):
        # The prompt did not offer this verdict on this turn (no continuable
        # anchor, or no participant section) — picking it is not an answer we
        # can land, and landing it anyway would be a guess.
        return f"verdict '{output.verdict}' was not offered on this turn"
    if output.verdict == VERDICT_MATCH and not 0 <= output.match_index < len(inp.menu):
        return "match index outside the menu"
    if output.verdict == VERDICT_PARTICIPANT and not (
        0 <= output.match_index < len(inp.participants)
    ):
        return "participant index outside the participant list"
    return None


def resolve_choice(
    decision: MergedRoutingDecision, inp: MergedRoutingInput
) -> Optional[str]:
    """The narrative id a `match` / `participant` verdict names.

    Bounds were already validated in `decide`; this is the lookup, kept next to
    the contract it depends on rather than inlined at the call site.
    """
    if decision.verdict == VERDICT_MATCH:
        return inp.menu[decision.match_index]["id"]
    if decision.verdict == VERDICT_PARTICIPANT:
        return inp.participants[decision.match_index]["id"]
    return None
