"""
@file_name: prompts_merged.py
@author: NetMind.AI
@date: 2026-08-27
@description: The merged-routing instruction fragments and their per-turn
              composer (`build_merged_instructions`).

Split from prompts.py on review (2026-08-27 round 3, I3): the merged section
had grown to ~220 lines inside an already-long shared prompt module. The
no-durable-topic rubric stays in prompts.py — it is spliced verbatim into
the judge variants too, and THE one copy is the whole point (PR #361 round
2, I2); this module imports it.
"""

from __future__ import annotations

from .prompts import _NO_DURABLE_TOPIC_RUBRIC

#: Shared by every variant — nothing here presupposes a continuable anchor
#: (review round 2, C1: the asymmetry block used to live here and told every
#: variant "staying is the DEFAULT", inviting the one verdict the contract
#: refuses on anchorless/bucket turns).
_MERGED_ROUTING_CORE = """You decide, in one step, where the user's current message belongs.

**Judgment granularity — business intent level**

- Judge at the business intent / goal level, NOT at the message-detail level.
- Sub-topic shifts, progress updates, status reports, acknowledgements and
  follow-up instructions within the same business goal all belong to the SAME
  thread.
- Different channels or senders do NOT define thread boundaries — only the
  business content does.
- Only decide "different" when the user introduces a genuinely new, unrelated
  business intent.

**How to read the menu**

Each row shows which query terms matched and where. Judge on those terms: a row
carried entirely by frame words (帮/查/一/下, "the", "how", "me") is a lexical
accident, not a topic match. Word overlap is a necessary but not sufficient
condition for choosing any thread: a row must overlap to be considered, and
overlapping is not by itself a reason to choose it."""


#: Rendered only when the anchored thread is actually continuable — every
#: sentence here presupposes a thread the turn may stay on.
_MERGED_CORE_WITH_ANCHOR = """**The asymmetry — read this before anything else**

The conversation is already on a thread (shown below as the anchored thread).
Staying on that thread is the DEFAULT answer. The keyword menu is evidence for
LEAVING it, never evidence for staying: two consecutive messages in one
continuous thread routinely share no words at all, so an anchored thread that
appears nowhere in the menu — or scores nothing — is the normal case, not a
signal.

Concretely: never leave the anchored thread because a menu row looks lexically
closer. Leave it only when the message pursues a different business goal.

**The previous turn decides most of these**

- If the Agent's own reply introduced a sub-topic and the user is following up
  on it, that is still the same thread — including when the user's message
  would be unreadable without that reply ("讲第一个", "the second one", "why?").
- If the Agent's reply closed a topic and the user opens an unrelated one, that
  is not the same thread.
- Long elapsed time is weak evidence on its own; combined with a changed
  subject it points away from the anchored thread."""


#: The symmetric explanation for turns with nothing to stay on — the anchor
#: section in the user input says WHY (absent, or a legacy container).
_MERGED_CORE_WITHOUT_ANCHOR = """**No thread to stay on — read this before anything else**

This message is not on any continuable thread (the anchor section below says
why). Place it on its own merits among the answers offered. The previous turn
is still shown: use it to READ an elliptical message ("the second one",
"why?") — what the message is about may live entirely in that reply — and then
route the subject you find, not the words."""


_MERGED_ROUTING_OUTPUT_CONTRACT = """Requirements:
- Give your reasoning, then exactly one verdict.
- match_index is 0-based and refers to the section your verdict names. Use -1
  for every verdict that names no candidate.
- Never invent an index. If the answer you want has no candidate behind it,
  the answer is new or no_topic."""


NARRATIVE_MERGED_ROUTING_INSTRUCTIONS_HEADER = (
    "You are a conversation thread router."
)

# ── merged-instruction fragments ────────────────────────────────────────────
# One definition per answer; `build_merged_instructions` composes them by what
# the turn actually offers. The answer table, the priority list, and the
# output-format verdict list are all derived from the same selection, so a
# verdict the contract will refuse can never be invited by the prose
# (review Critical 1: the shared core used to list continue_anchor as "the
# default" even when the anchor was a legacy container, and every model that
# obeyed it landed in merged_fallback_new — the D19 shape).

_MERGED_ANSWER_CONTINUE = """- continue_anchor — the message belongs to the anchored thread shown below.
  This is the default; choose it whenever the message pursues the same business
  goal, follows up on the Agent's last reply, or cannot be read without it."""

_MERGED_ANSWER_PARTICIPANT = """- participant — the message is about one of the participant-associated threads.
  Give its index within that section."""

_MERGED_ANSWER_MATCH = """- match — the message belongs to one of the menu threads. Give its index
  within the menu. Requires a real subject overlap, not just shared words."""

_MERGED_ANSWER_NEW = """- new — the message introduces a real subject that none of the threads above
  covers."""

_MERGED_ANSWER_NO_TOPIC = """- no_topic — the message carries nothing worth remembering as its own thread."""

_MERGED_PARTICIPANT_PREAMBLE = """**This user is a PARTICIPANT in other threads**

Someone else started those threads and invited this user into them. They are
listed in their own section, ahead of the keyword menu, and that order is the
priority rule: a task the user was invited into outranks a keyword hit on the
user's own thread."""

#: Appended to the preamble only when continuing is actually on offer — the
#: sentence names continue_anchor, and naming it on a turn where the contract
#: refuses it is the exact defect the builder exists to prevent.
_MERGED_PARTICIPANT_ANCHOR_SENTENCE = """ It does NOT outrank the anchored
thread — if the message continues what the conversation is already doing,
continue_anchor still wins."""

_PRIORITY_CONTINUE = (
    "Answer continue_anchor when the message pursues the same business goal "
    "as the anchored thread."
)
_PRIORITY_PARTICIPANT = (
    "Answer participant when the message is about one of the "
    "participant-associated threads."
)
_PRIORITY_MATCH = (
    "Answer match when a menu thread's subject genuinely covers the message."
)
_PRIORITY_TAIL = (
    "If none of the above applies, decide whether the message carries a\n"
    "   durable topic at all: a real subject → new; nothing to remember →\n"
    "   no_topic."
)


def build_merged_instructions(
    *, anchor_is_continuable: bool, with_participants: bool, with_menu: bool = True
) -> str:
    """Compose the merged-routing instructions for what THIS turn offers.

    Composition, never four literals: the judge's two variants forked three
    times because shared text was hand-copied (routing_blocks.py header), and
    2×2 variants hand-written would fork faster.
    """
    answers = []
    verdict_names = []
    if anchor_is_continuable:
        answers.append(_MERGED_ANSWER_CONTINUE)
        verdict_names.append("continue_anchor")
    if with_participants:
        answers.append(_MERGED_ANSWER_PARTICIPANT)
        verdict_names.append("participant")
    if with_menu:
        # An empty menu offers no index to give — listing `match` anyway is
        # the round-2 Critical one door over: the prose invites a verdict the
        # contract is guaranteed to refuse (review round 6, I3). The menu
        # HEADER still renders its empty note; only the answer goes away.
        answers.append(_MERGED_ANSWER_MATCH)
        verdict_names.append("match")
    answers += [_MERGED_ANSWER_NEW, _MERGED_ANSWER_NO_TOPIC]
    verdict_names += ["new", "no_topic"]

    priority_items = []
    if anchor_is_continuable:
        priority_items.append(_PRIORITY_CONTINUE)
    if with_participants:
        priority_items.append(_PRIORITY_PARTICIPANT)
    if with_menu:
        priority_items.append(_PRIORITY_MATCH)
    priority_items.append(_PRIORITY_TAIL)
    # Complete sentences, numbered — the order IS the rule ("first match
    # wins"), so no lexical splicing is needed to say "otherwise" (round 8,
    # M4: the spliced form left fragments from item 2 on).
    priority_lines = "\n".join(
        f"{i}. {item}" for i, item in enumerate(priority_items, start=1)
    )

    participant_section = ""
    if with_participants:
        participant_section = _MERGED_PARTICIPANT_PREAMBLE
        if anchor_is_continuable:
            participant_section += _MERGED_PARTICIPANT_ANCHOR_SENTENCE
        participant_section += "\n\n"

    verdict_list = ", ".join(f'"{v}"' for v in verdict_names)
    answer_count = len(answers)
    anchor_core = (
        _MERGED_CORE_WITH_ANCHOR
        if anchor_is_continuable
        else _MERGED_CORE_WITHOUT_ANCHOR
    )

    return f"""{NARRATIVE_MERGED_ROUTING_INSTRUCTIONS_HEADER}

{anchor_core}

{_MERGED_ROUTING_CORE}

{participant_section}**Your {answer_count} answers**

{chr(10).join(answers)}

{_NO_DURABLE_TOPIC_RUBRIC}

Priority when you are torn (first match wins):
{priority_lines}

{_MERGED_ROUTING_OUTPUT_CONTRACT}

Output format:
class MergedRoutingOutput(BaseModel):
    reason: str        # your reasoning
    verdict: str       # one of: {verdict_list}
    match_index: int   # 0-based index into the section your verdict names, else -1
"""


