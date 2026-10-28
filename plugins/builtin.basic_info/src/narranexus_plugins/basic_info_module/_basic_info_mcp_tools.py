"""
@file_name: _basic_info_mcp_tools.py
@author: Bin Liang
@date: 2026-05-20
@description: MCP server + narrative-awareness tools for BasicInfoModule (Fix #2 P3).

The agent's conversation history (built by ChatModule.gather) is a
single time-sorted timeline merging the current thread (full) with the latest
~30 lines across the user's OTHER threads, each line tagged
`[<time> · <topic> · nar=<narrative_id> · evt=<event_id>]`, plus a separate
"recent background activity" list (each with an evt= id). The system PICKS a
default narrative for the turn, but that pick isn't always right. These four
tools give the agent visibility + agency over it:

  - view_narrative(narrative_id): full info on a thread incl. ALL its chat
    history (the timeline only shows the latest trimmed slice).
  - view_event(event_id): one past turn's full agent-loop / reasoning detail
    (the timeline only carries the message that was sent to the user).
  - switch_narrative(narrative_id): declare that THIS turn belongs to that
    existing thread — the runtime re-attributes this turn's event + memory.
  - create_narrative(title, description): declare THIS turn starts a NEW thread.

switch/create are SIGNALS. The tool validates/creates and returns the target id;
the agent_runtime hook (step_4_persist_results) detects the call and does the
actual re-attribution. The tool process and the runtime are different processes,
so detection-from-the-tool-call is how they communicate (no shared state).
"""
from __future__ import annotations

import time
from collections import OrderedDict

from loguru import logger
from mcp.server.fastmcp import FastMCP


# Tool names the runtime hook scans agent_loop_response for (keep in lockstep
# with step_4_persist_results._detect_narrative_routing_signal).
SWITCH_NARRATIVE_TOOL = "switch_narrative"
CREATE_NARRATIVE_TOOL = "create_narrative"


# ── submit_feedback dedup (2026-09-09) ───────────────────────────────────────
# Feedback triggers (a)/(b) are rate-limited by human interaction: the user has
# to complain, or the same instruction has to fail twice. Trigger (c) is
# machine-generated — a platform-side outage reproduces on EVERY tool call, and
# it is platform-WIDE, so every affected agent hits it every turn. "File it
# once" therefore cannot be left to the model's memory: a long agent_loop
# (铁律 #14) gets its context compacted, and this tool always answers ok=True,
# which reinforces re-filing. The gate lives here so the agent's own
# confirmation enforces the rule.
#
# Single-process assumption (铁律 #20): the basic_info MCP server is one process
# shared by the deployment — this does NOT dedup across processes or restarts,
# and it is deliberately not a substitute for intake-side idempotency. Bounded
# by TTL + entry count, and BOTH halves of the slot are truncated: agent_id and
# dedup_key are model-supplied, so neither may grow an entry without limit.
FEEDBACK_DEDUP_TTL_SECONDS = 6 * 3600
FEEDBACK_DEDUP_MAX_ENTRIES = 4096
FEEDBACK_DEDUP_KEY_MAXLEN = 120

# A reservation has THREE states, not two. Modelling only "present / absent"
# is what let a concurrent duplicate claim the team had been notified while the
# only send that ever ran was still in flight — and then failed. The record is
# ``[monotonic stamp, delivered?]``; the flag is flipped IN PLACE on success so
# the key keeps its insertion position (see _dedup_reserve's front sweep).
DEDUP_NEW = "new"              # we own the slot and must do the send
DEDUP_PENDING = "pending"      # someone else is mid-send; outcome unknown
DEDUP_CONFIRMED = "confirmed"  # a report for this slot really was delivered

_feedback_dedup: "OrderedDict[tuple[str, str], list]" = OrderedDict()


def _dedup_slot(agent_id: str, dedup_key: str) -> tuple[str, str]:
    return (
        agent_id[:FEEDBACK_DEDUP_KEY_MAXLEN],
        dedup_key[:FEEDBACK_DEDUP_KEY_MAXLEN],
    )


def _dedup_reserve(slot: tuple[str, str]) -> tuple[str, list | None]:
    """Claim `slot`, returning ``(state, record)``.

    ``record`` is non-None only for ``DEDUP_NEW`` — the caller that owns
    the reservation is the only one allowed to settle it.

    Reserving BEFORE the send (rather than recording after it) is what closes
    the window two concurrent calls would otherwise both pass through. Records
    are inserted once and their stamp is never rewritten, so insertion order is
    time order and expiry can be swept from the front.
    """
    now = time.monotonic()
    while _feedback_dedup:
        oldest, rec = next(iter(_feedback_dedup.items()))
        if now - rec[0] < FEEDBACK_DEDUP_TTL_SECONDS:
            break
        _feedback_dedup.pop(oldest, None)

    existing = _feedback_dedup.get(slot)
    if existing is not None:
        # No record handed back: only the caller that OWNS a reservation may
        # confirm or release it, so a non-NEW caller is given nothing it could
        # settle by mistake.
        return (DEDUP_CONFIRMED if existing[1] else DEDUP_PENDING), None

    record = [now, False]
    _feedback_dedup[slot] = record
    while len(_feedback_dedup) > FEEDBACK_DEDUP_MAX_ENTRIES:
        _feedback_dedup.popitem(last=False)
    return DEDUP_NEW, record


def _dedup_confirm(slot: tuple[str, str], record: list) -> None:
    """Mark OUR reservation delivered, in place.

    Mutating the value (not re-inserting the key) is deliberate: it preserves
    the insertion-order == time-order invariant the front sweep depends on.
    The identity check means a reservation that TTL- or capacity-eviction
    already removed — and that some later call re-created — is not stamped
    with our outcome.
    """
    if _feedback_dedup.get(slot) is record:
        record[1] = True


def _dedup_release(slot: tuple[str, str], record: list) -> None:
    """Undo OUR reservation when the send did not reach the intake.

    Without this the cache would hold a FAILURE sentinel: an intake outage
    would silently consume the one report this agent+code is allowed to make,
    and the team would never learn about the platform error. The caller settles
    its slot on EVERY exit — return, raise or cancellation — so a send that did
    not deliver behaves exactly like the un-deduped code (the next call tries
    again); only a DELIVERED report suppresses repeats.

    The identity check stops a late failure from deleting a DIFFERENT call's
    live reservation after eviction recycled the slot.
    """
    if _feedback_dedup.get(slot) is record:
        del _feedback_dedup[slot]


# ── What the agent may tell the user (2026-09-09) ────────────────────────────
# "The NarraNexus team has been notified" is a claim about what THIS call did,
# so it belongs in this call's result, not in a standing prompt rule. The
# prompt used to assert it unconditionally for platform-error reports, which
# made the agent tell the user the team was on it even when the POST had
# silently failed (send_feedback swallows everything and only DEBUG-logs) or
# when feedback is disabled entirely for this deployment. The agent cannot see
# any of that; this function can.
#
# The default across the product stays "don't mention telemetry to the user"
# (prompts.py Product Feedback Duty, Rules). The one case that overrides it is
# a DELIVERED `error` report: the user is sitting in front of a platform-side
# failure right now, and "someone has been told" is the only true, useful thing
# we can offer them.
# `outcome` values, in the order _feedback_result handles them.
FEEDBACK_DISABLED = "disabled"        # this deployment reports nothing at all
FEEDBACK_NO_SUMMARY = "no_summary"    # nothing to file; never left the process
FEEDBACK_PENDING = "pending"          # a concurrent call is still in flight
FEEDBACK_UNDELIVERED = "undelivered"  # the send ran and did not land
FEEDBACK_DUPLICATE = "duplicate"      # an earlier call for this slot delivered
FEEDBACK_DELIVERED = "delivered"      # this call reached the intake


def _feedback_result(*, category: str, outcome: str) -> dict:
    if outcome == FEEDBACK_DISABLED:
        return {
            "ok": True,
            "notified": False,
            "message": (
                "Feedback reporting is switched off for this deployment, so no "
                "one was told — do NOT tell the user the team has been "
                "notified. Nothing for you to retry. Keep working on the "
                "user's problem."
            ),
        }
    if outcome == FEEDBACK_NO_SUMMARY:
        # The one outcome the agent can actually act on, so it is the one
        # outcome that asks for another call.
        return {
            "ok": True,
            "notified": False,
            "message": (
                "Nothing was filed — `summary` was empty. Call again with ONE "
                "sentence describing the problem in your own words. Do not "
                "tell the user the team has been notified."
            ),
        }
    if outcome == FEEDBACK_PENDING:
        # Another call already owns this slot and has not come back yet. Its
        # send may still fail, so the honest answer is "not yet", never
        # "already reported" — that mistake is precisely what this branch
        # exists to prevent. The no-retry rule matters most here: this is the
        # message a model sees over and over during an outage, and an open
        # question would invite it to re-file under a fresh key, which is
        # exactly the flood the dedup gate exists to stop.
        return {
            "ok": True,
            "notified": False,
            "message": (
                "A report for this exact problem is already on its way; yours "
                "was not sent again. Its outcome is not known yet, so do NOT "
                "tell the user the team has been notified. Nothing for you to "
                "retry, and do not re-file it under a different dedup_key. "
                "Keep working on the user's problem."
            ),
        }
    if outcome == FEEDBACK_UNDELIVERED:
        return {
            "ok": True,
            "notified": False,
            "message": (
                "Could not reach the NarraNexus team just now — do NOT tell the "
                "user they were notified. Nothing for you to retry or apologise "
                "for; keep working on the user's problem."
            ),
        }
    if category != "error":
        return {
            "ok": True,
            "notified": True,
            "message": "Feedback recorded. Keep working on the user's problem.",
        }
    already = "Already reported" if outcome == FEEDBACK_DUPLICATE else "Reported"
    return {
        "ok": True,
        "notified": True,
        "message": (
            f"{already} — the NarraNexus team has been notified. You may tell "
            "the user that much, but still do not claim a cause. Keep working "
            "on the user's problem."
        ),
    }


def create_basic_info_mcp_server() -> FastMCP:
    """Create the BasicInfoModule MCP server with the narrative + feedback tools."""
    mcp = FastMCP("basic_info_module")
    _register_narrative_tools(mcp)
    _register_feedback_tool(mcp)
    logger.info(
        "BasicInfo MCP: tools registered "
        f"(view_narrative, view_event, {SWITCH_NARRATIVE_TOOL}, {CREATE_NARRATIVE_TOOL}, "
        f"submit_feedback)"
    )
    return mcp


def _register_feedback_tool(mcp: FastMCP) -> None:
    """submit_feedback — the agent's channel for telling the NarraNexus team
    something went wrong.

    Privacy: feedback_client enforces what it CAN (identifiers hashed, summary
    truncated); the CONTENT of the summary — no user quotes, no keys/PII — is
    prompt-governed (prompts.py Product Feedback Duty) and not verifiable in
    code. The tool always answers ok=True — delivery is fire-and-forget and
    the agent must not retry or dwell on it, including when a report is
    suppressed as a duplicate (see the dedup block above). What the agent is
    allowed to TELL THE USER about the report travels in the result, not in the
    prompt — see _feedback_result."""

    @mcp.tool(
        name="submit_feedback",
        description=(
            "Report a product problem to the NarraNexus team. Call when (a) the "
            "user expresses dissatisfaction, frustration or disappointment with "
            "how you/the product behaved, (b) you have failed the SAME user "
            "instruction 2+ times in a row, or (c) a credential / endpoint / "
            "quota the PLATFORM injects for you is rejected by a platform tool "
            "(`agent-token-invalid`, an unexpected 401/403 from a working "
            "binding) — file it with category `error` naming the tool and the "
            "error code, even if a retry later works, and pass "
            "`dedup_key=\"<tool>:<code>\"`: this tool then files it ONCE per "
            "agent per tool+code and silently drops repeats, so you never have "
            "to remember what you already filed. Not (c): `no_credential` when "
            "nothing is bound, by-design policy refusals like "
            "`official-agent-required`, or a secret the user just "
            "typed being rejected. If a failure satisfies both (b) and (c), "
            "file it as (c) only. `category` is one of: "
            "user_dissatisfaction | repeated_failure | error | feature_gap | other. "
            "`severity` is low | medium | high. `summary` must be ONE sentence "
            "describing the PROBLEM in your own words — never quote the user's "
            "messages, never include personal names, secrets/keys or file "
            "contents (the TOOL name and the error code are required for (c) — "
            "they are not secrets). This tool "
            "informs the developers; it does NOT solve the user's issue — still "
            "handle the user yourself. Read the result: it reports whether the "
            "team was actually reached (`notified`) and whether you may pass "
            "that on to the user. Never tell the user the team has been "
            "notified unless this call said so."
        ),
    )
    async def submit_feedback(
        agent_id: str,
        user_id: str,
        category: str,
        summary: str,
        severity: str = "medium",
        dedup_key: str = "",
    ) -> dict:
        from narranexus.platform.integrations.feedback_client import (
            CATEGORIES,
            feedback_disabled,
            send_feedback,
        )

        # Normalise once, here: send_feedback coerces an unknown category to
        # "other" before posting, and _feedback_result keys off the category
        # too. Left un-normalised the two halves of this tool would disagree
        # about what the same report is.
        if category not in CATEGORIES:
            category = "other"

        # Both of these end the call before any slot is claimed, so a
        # deployment that reports nothing — and a call with nothing to report —
        # never churn the dedup cache. send_feedback would drop an empty
        # summary silently and return False, which would otherwise be
        # described to the agent as "could not reach the team".
        if feedback_disabled():
            return _feedback_result(category=category, outcome=FEEDBACK_DISABLED)
        if not (summary or "").strip():
            return _feedback_result(category=category, outcome=FEEDBACK_NO_SUMMARY)

        # dedup_key is optional on purpose: triggers (a)/(b) call without it and
        # must keep working. An empty key means "no dedup", never "dedup all".
        slot = record = None
        if dedup_key:
            slot = _dedup_slot(agent_id, dedup_key)
            state, record = _dedup_reserve(slot)
            if state != DEDUP_NEW:
                logger.info(
                    f"[feedback] suppressed as {state} category={category} "
                    f"agent={slot[0]!r} dedup_key={slot[1]!r}"
                )
                # ok=True, never an error: the tool contract forbids the agent
                # from retrying or apologising about telemetry.
                outcome = (
                    FEEDBACK_DUPLICATE if state == DEDUP_CONFIRMED else FEEDBACK_PENDING
                )
                return _feedback_result(category=category, outcome=outcome)

        delivered = False
        try:
            delivered = await send_feedback(
                category=category,
                summary=summary,
                severity=severity,
                source="agent",
                agent_id=agent_id,
                user_id=user_id,
            )
        finally:
            # Settle the reservation on EVERY exit, cancellation included.
            # send_feedback's `except Exception` does not cover
            # asyncio.CancelledError, so a cancelled tool call would otherwise
            # leave the slot PENDING for the whole TTL — muting this agent and
            # error code for six hours while telling every later call that a
            # report is "already on its way" when none ever left the process.
            if slot is not None and record is not None:
                if delivered:
                    _dedup_confirm(slot, record)
                else:
                    _dedup_release(slot, record)

        # Outside the try on purpose: a cancelled request was never answered,
        # and logging delivered=False for it would invent an outcome.
        logger.info(
            f"[feedback] agent report category={category} severity={severity} "
            f"agent={agent_id!r} delivered={delivered}"
        )
        # Always ok — the agent shouldn't retry or apologise about telemetry.
        return _feedback_result(
            category=category,
            outcome=FEEDBACK_DELIVERED if delivered else FEEDBACK_UNDELIVERED,
        )


def _register_narrative_tools(mcp: FastMCP) -> None:

    @mcp.tool(
        name="view_narrative",
        description=(
            "Look up one conversation thread (narrative) IN FULL by its id — "
            "including its entire chat history. Your conversation history is a "
            "merged, time-trimmed timeline (latest ~30 lines across threads), so "
            "an older thread may be partly cut off. Take a narrative_id from any "
            "message tag `[.. nar=<id> ..]` and pass it here to read that whole "
            "thread before deciding how to respond. Returns {name, description, "
            "summary, keywords, message_count, messages:[{time, role, content, "
            "event_id}]}."
        ),
    )
    async def view_narrative(agent_id: str, narrative_id: str) -> dict:
        # Routes through the AgentDataStore seam (DirectStore locally / HttpStore
        # in cloud). The old raw MySQL is gone — the read is now dialect-safe and
        # scoped to this agent (the tool used to return ANY agent's narrative).
        from narranexus.platform.module_system.data_access import get_agent_data_store

        return await get_agent_data_store().view_narrative(agent_id, narrative_id)

    @mcp.tool(
        name="view_event",
        description=(
            "Get one past turn's FULL detail by its event id (the `evt=<id>` in "
            "a timeline message tag or a recent-activity line). The timeline only "
            "shows the message you SENT; this returns that turn's full agent-loop "
            "trace and reasoning. Returns {final_output, trigger, time, event_log}."
        ),
    )
    async def view_event(agent_id: str, event_id: str) -> dict:
        # Routes through the seam (see view_narrative). The raw `trigger`
        # backtick MySQL is gone; the read is dialect-safe and agent-scoped.
        from narranexus.platform.module_system.data_access import get_agent_data_store

        return await get_agent_data_store().view_event(agent_id, event_id)

    @mcp.tool(
        name=SWITCH_NARRATIVE_TOOL,
        description=(
            "Declare that THIS turn belongs to a DIFFERENT existing thread than "
            "the one the system defaulted to. Use it when the user's message "
            "(especially a short reply like '好'/'yes') is actually continuing "
            "another thread shown in your timeline — pass that thread's "
            "narrative_id (from its `[.. nar=<id> ..]` tag). The system will "
            "re-file this turn into that thread so future context stays correct. "
            "Call this BEFORE you reply. If none fits and it's a new topic, use "
            "create_narrative instead."
        ),
    )
    async def switch_narrative(agent_id: str, narrative_id: str) -> dict:
        # Routes through the seam (see view_narrative). Now agent-scoped: the old
        # raw SQL validated existence but not ownership. The result key is
        # `success` (aligned with the other seam tools), not the old `ok`.
        from narranexus.platform.module_system.data_access import get_agent_data_store

        return await get_agent_data_store().switch_narrative(agent_id, narrative_id)

    @mcp.tool(
        name=CREATE_NARRATIVE_TOOL,
        description=(
            "Declare that THIS turn starts a brand-NEW conversation thread "
            "(topic) — use it when the user's message doesn't belong to any "
            "thread in your timeline. Provide a short title and one-line "
            "description. The system creates the thread and files this turn into "
            "it. Call this BEFORE you reply. Returns {narrative_id}."
        ),
    )
    async def create_narrative(agent_id: str, user_id: str, title: str, description: str = "") -> dict:
        # SIGNAL only: the runtime hook (step_4) reads {title, description} from
        # this call, CREATES the narrative, and files this turn into it. We do
        # not create here so the tool process and runtime don't double-create.
        if not (title or "").strip():
            return {"ok": False, "error": "title is required"}
        logger.info(f"[NarrativeTool] create_narrative signal: title={title!r} (agent={agent_id})")
        return {
            "ok": True,
            "title": title,
            "message": "Noted — a new narrative with this title will be created and this turn filed into it.",
        }
