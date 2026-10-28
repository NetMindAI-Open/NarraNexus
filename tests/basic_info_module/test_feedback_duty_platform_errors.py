"""
@file_name: test_feedback_duty_platform_errors.py
@date: 2026-09-09
@description: Regression guard for the "Product Feedback Duty" section of the
BasicInfo system-prompt template, the submit_feedback tool description, and the
tool's once-per-agent-per-code dedup gate.

Prod 2026-09-09: an agent's platform-run CLI answered ``agent-token-invalid``
(root cause: the platform sent the token to the wrong backend). The agent never
filed feedback (the duty only fired on user complaints / two failures of the
same instruction), asserted a wrong diagnosis to the user as fact ("the platform
cached an expired token"), and pasted the raw token into chat to "compare" it.
The template must (1) make a rejected PLATFORM-injected credential a feedback
trigger while excluding expected answers (``no_credential`` = not bound,
by-design policy refusals, a user-typed secret being rejected), (2) forbid
asserting a cause the agent cannot verify for those errors only, (3) forbid
pasting tokens / credential files into messages.

Trigger 3 is machine-generated — a platform-wide outage reproduces on every
tool call — so "file it once" is enforced in ``_basic_info_mcp_tools`` rather
than left to the model's memory; the dedup tests pin that gate, including the
release-on-failed-delivery rule that keeps the cache from holding a failure
sentinel.
"""
from __future__ import annotations

import pytest


def _templates() -> list[str]:
    # Both the legacy template and the relocated STABLE one are rendered in
    # prod depending on settings; the rule must live in both. Prose is
    # hard-wrapped, so fold whitespace before phrase checks.
    from narranexus_plugins.basic_info_module.prompts import (
        BASIC_INFO_MODULE_INSTRUCTIONS,
        BASIC_INFO_MODULE_INSTRUCTIONS_STABLE,
    )

    return [" ".join(t.split()) for t in (
        BASIC_INFO_MODULE_INSTRUCTIONS, BASIC_INFO_MODULE_INSTRUCTIONS_STABLE,
    )]


def _duty_section(text: str) -> str:
    # Slice to the NEXT `#### ` heading, not to the first ` --- `: a horizontal
    # rule added inside this (still-growing) section would silently shrink the
    # slice and leave every guard below reading only its first half.
    start = text.index("#### Product Feedback Duty")
    return text[start: text.index("#### ", start + len("#### Product Feedback Duty"))]


def _feedback_tool() -> tuple[str, object]:
    """Return (tool description, the registered submit_feedback coroutine)."""
    from narranexus_plugins.basic_info_module import _basic_info_mcp_tools as mt

    captured: dict = {}

    class _Mcp:
        def tool(self, **kw):
            def _wrap(fn):
                captured[kw["name"]] = (kw["description"], fn)
                return fn
            return _wrap

    mt._register_feedback_tool(_Mcp())
    return captured["submit_feedback"]


def _tool_description() -> str:
    return _feedback_tool()[0]


@pytest.fixture
def clean_dedup():
    from narranexus_plugins.basic_info_module import _basic_info_mcp_tools as mt

    mt._feedback_dedup.clear()
    yield mt
    mt._feedback_dedup.clear()


# ── Prompt template ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
def test_the_duty_section_slice_reaches_its_last_rule(text):
    # Guards the guard: every assertion below is scoped to this slice, and the
    # last rule in the section (the no-paste red line) is its most load-bearing.
    duty = _duty_section(text)
    assert duty.rstrip().rstrip("-").rstrip().endswith("not to compare two of them.")


@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
def test_rejected_platform_credential_is_a_feedback_trigger(text):
    duty = _duty_section(text)
    third = duty[duty.index("3. "):duty.index("Rules:")]
    assert "agent-token-invalid" in third
    assert "category `error`" in third
    # Fires even when a workaround later succeeds, once per tool + code —
    # enforced by the tool via dedup_key, not by the model's memory.
    assert "workaround" in third
    assert "dedup_key" in third
    assert "ONCE per agent per tool + code" in third


@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
def test_expected_answers_are_excluded_from_the_trigger(text):
    duty = _duty_section(text)
    third = duty[duty.index("3. "):duty.index("Rules:")]
    # no_credential / not bound is the normal reply of every channel tool
    # when nothing is bound; official-agent-required is a by-design policy
    # answer; a user-typed secret being rejected is the user's fix, not ours.
    assert "does NOT cover" in third
    assert "`no_credential`" in third
    assert "official-agent-required" in third
    assert "user just typed" in third


@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
def test_trigger_three_wins_when_trigger_two_also_fires(text):
    # A platform credential failing the same instruction twice satisfies both;
    # without a stated precedence the same incident lands in two categories.
    duty = _duty_section(text)
    third = duty[duty.index("3. "):duty.index("Rules:")]
    assert "also satisfies trigger 2" in third
    assert "trigger 3 only" in third


@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
def test_the_tool_name_and_error_code_are_not_covered_by_never_include_names(text):
    # "never include names" means PII; ten lines earlier the same section
    # REQUIRES the tool name and the error code. Without the carve-out a
    # cautious model files "a platform credential was rejected" — useless for
    # triage, which is the entire value of trigger 3.
    duty = _duty_section(text)
    rules = duty[duty.index("Rules:"):duty.index("Be conservative")]
    assert "personal names" in rules
    assert "The TOOL name and the error code are required" in rules
    assert "they are not secrets" in rules


@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
def test_no_diagnosis_rule_is_scoped_to_platform_injected_credentials(text):
    duty = _duty_section(text)
    para = duty[duty.index("Be conservative"):]
    assert "do NOT assert a diagnosis" in para
    assert "MIGHT mean" in para
    # The carve-out: user-provided secrets and BYOK keys keep concrete guidance.
    assert "BYOK" in para
    assert "applies to credentials the user provided" in para


@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
def test_the_prompt_never_asserts_that_the_team_was_notified(text):
    # "The team has been notified" is the OUTCOME of the submit_feedback call,
    # not a standing rule: the send is fire-and-forget with every exception
    # swallowed, and a whole deployment can have feedback switched off. A
    # prompt that asserts it makes the agent tell the user something the
    # platform may not have done. The claim therefore lives in the tool result.
    duty = _duty_section(text)
    para = duty[duty.index("Be conservative"):]
    assert "OUTCOME of your submit_feedback call" in para
    assert "relay only what it says" in para
    assert "Never tell the user the team has been notified on your own authority" in para

    # The general default survives, with exactly one escape hatch — the tool's
    # own result — instead of a second imperative buried in another paragraph.
    rules = duty[duty.index("Rules:"):duty.index("Be conservative")]
    assert "don't announce that you filed feedback unless the user asked" in rules
    assert "unless submit_feedback's own result tells you to pass something on" in rules


@pytest.mark.parametrize("text", _templates(), ids=["legacy", "stable"])
def test_prompt_forbids_pasting_credentials(text):
    para = _duty_section(text)
    para = para[para.index("Be conservative"):]
    assert "never paste a token" in para
    assert "credential file" in para


# ── Tool description ────────────────────────────────────────────────────────

def test_tool_description_names_the_platform_error_trigger():
    desc = _tool_description()
    assert "(c)" in desc
    assert "`agent-token-invalid`" in desc
    assert "category `error`" in desc
    assert "Not (c)" in desc and "`no_credential`" in desc
    assert "`official-agent-required`" in desc


def test_tool_description_tells_the_agent_to_pass_a_dedup_key():
    desc = _tool_description()
    assert 'dedup_key="<tool>:<code>"' in desc
    assert "ONCE per agent per tool+code" in desc
    # The agent must not be asked to remember what it filed.
    assert "never have to remember" in desc


def test_tool_description_states_the_b_vs_c_precedence():
    assert "satisfies both (b) and (c)" in _tool_description()


def test_tool_description_points_the_agent_at_the_result_for_the_notification():
    desc = _tool_description()
    assert "`notified`" in desc
    assert "Never tell the user the team has been notified unless this call said so" in desc


def test_dedup_key_is_optional_so_triggers_a_and_b_keep_working():
    import inspect

    sig = inspect.signature(_feedback_tool()[1])
    assert sig.parameters["dedup_key"].default == ""


# ── Dedup gate ──────────────────────────────────────────────────────────────

async def _call(mt, monkeypatch, fn, *, delivered=True, send=None, **kw):
    sent: list[dict] = []

    async def _fake_send(**payload):
        sent.append(payload)
        if send is not None:
            return await send(payload)
        return delivered

    monkeypatch.setattr(
        "narranexus.platform.integrations.feedback_client.send_feedback", _fake_send
    )
    result = await fn(
        agent_id=kw.pop("agent_id", "agent_a"),
        user_id="user_1",
        category=kw.pop("category", "error"),
        summary=kw.pop("summary_override", "narra_cli rejected a platform token"),
        **kw,
    )
    return result, sent


@pytest.mark.asyncio
async def test_a_delivered_report_suppresses_later_repeats_of_the_same_code(
    clean_dedup, monkeypatch
):
    fn = _feedback_tool()[1]
    first, sent = await _call(clean_dedup, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid")
    assert first["ok"] is True and len(sent) == 1

    second, sent2 = await _call(clean_dedup, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid")
    # Still ok=True — a failure return would make the agent retry or apologise.
    assert second["ok"] is True
    assert sent2 == []
    # A reservation only survives a delivered send, so a duplicate hit is proof
    # the team really has this one — the agent may still say so.
    assert second["notified"] is True
    assert "Already reported" in second["message"]


@pytest.mark.asyncio
async def test_dedup_is_per_agent_and_per_code(clean_dedup, monkeypatch):
    fn = _feedback_tool()[1]
    await _call(clean_dedup, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid")

    _, other_agent = await _call(
        clean_dedup, monkeypatch, fn,
        dedup_key="narra_cli:agent-token-invalid", agent_id="agent_b",
    )
    assert len(other_agent) == 1

    _, other_code = await _call(
        clean_dedup, monkeypatch, fn, dedup_key="narra_cli:no_endpoint",
    )
    assert len(other_code) == 1


@pytest.mark.asyncio
async def test_no_dedup_key_means_no_dedup(clean_dedup, monkeypatch):
    # Triggers (a)/(b) call without the key; two complaints are two reports.
    fn = _feedback_tool()[1]
    _, first = await _call(clean_dedup, monkeypatch, fn)
    _, second = await _call(clean_dedup, monkeypatch, fn)
    assert len(first) == 1 and len(second) == 1
    assert not clean_dedup._feedback_dedup


@pytest.mark.asyncio
async def test_a_failed_delivery_does_not_consume_the_one_allowed_report(
    clean_dedup, monkeypatch
):
    # The cache must not hold a failure sentinel: if the intake was down, the
    # platform error has NOT been reported and the next call must try again.
    fn = _feedback_tool()[1]
    _, first = await _call(
        clean_dedup, monkeypatch, fn,
        delivered=False, dedup_key="narra_cli:agent-token-invalid",
    )
    assert len(first) == 1
    assert not clean_dedup._feedback_dedup

    _, second = await _call(
        clean_dedup, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid",
    )
    assert len(second) == 1


@pytest.mark.asyncio
async def test_expired_entries_stop_suppressing_and_are_swept(clean_dedup, monkeypatch):
    mt = clean_dedup
    fn = _feedback_tool()[1]
    await _call(mt, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid")

    # Age every entry past the TTL by rewriting its stored stamp — the clock
    # itself is left alone so nothing else in the process is affected.
    for rec in mt._feedback_dedup.values():
        rec[0] -= mt.FEEDBACK_DEDUP_TTL_SECONDS + 1
    stale = mt._dedup_slot("agent_zzz", "gone:stale")
    mt._feedback_dedup[stale] = [
        mt.time.monotonic() - mt.FEEDBACK_DEDUP_TTL_SECONDS - 1, True,
    ]

    _, sent = await _call(mt, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid")
    assert len(sent) == 1
    # …and the unrelated expired entry was swept rather than left to accumulate.
    assert stale not in mt._feedback_dedup


def test_the_cache_is_bounded_in_entries(clean_dedup):
    mt = clean_dedup
    for i in range(mt.FEEDBACK_DEDUP_MAX_ENTRIES + 50):
        mt._dedup_reserve(mt._dedup_slot("agent_a", f"tool:{i}"))
    assert len(mt._feedback_dedup) == mt.FEEDBACK_DEDUP_MAX_ENTRIES


# ── What the agent may tell the user ────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_delivered_error_report_lets_the_agent_say_the_team_was_notified(
    clean_dedup, monkeypatch
):
    result, _ = await _call(clean_dedup, monkeypatch, _feedback_tool()[1])
    assert result["notified"] is True
    assert "the NarraNexus team has been notified" in result["message"]
    assert "You may tell the user" in result["message"]
    # Still no cause-asserting, even once the team is on it.
    assert "do not claim a cause" in result["message"]


@pytest.mark.asyncio
async def test_an_undelivered_report_forbids_claiming_the_team_was_notified(
    clean_dedup, monkeypatch
):
    # send_feedback swallows every exception and returns False — it also
    # returns False when the deployment has feedback switched off. Either way
    # nobody was told, and the agent must not say otherwise to the user.
    result, _ = await _call(clean_dedup, monkeypatch, _feedback_tool()[1], delivered=False)
    assert result["ok"] is True          # never an error: no retry, no apology
    assert result["notified"] is False
    assert "do NOT tell the user they were notified" in result["message"]
    assert "You may tell the user" not in result["message"]


@pytest.mark.asyncio
async def test_other_categories_keep_the_dont_mention_it_default(
    clean_dedup, monkeypatch
):
    # Trigger 1/2 reports are not something the user is waiting on; the
    # standing "don't announce telemetry" default applies to them.
    fn = _feedback_tool()[1]
    for category in ("user_dissatisfaction", "repeated_failure", "feature_gap", "other"):
        result, _ = await _call(clean_dedup, monkeypatch, fn, category=category)
        assert result["notified"] is True
        assert "notified" not in result["message"]
        assert "tell the user" not in result["message"].lower()


# ── Concurrency: a reservation is a claim, not yet a delivery ───────────────

@pytest.mark.asyncio
async def test_a_concurrent_duplicate_never_claims_an_unfinished_send_succeeded(
    clean_dedup, monkeypatch
):
    """The hole a two-state reservation leaves.

    One agent serves many sessions at once, and a platform-wide outage makes
    every one of them hit the same tool + code within the same seconds.
    send_feedback has a 3 s timeout and swallows its exception, so the window
    where a slot is claimed but undelivered is up to three seconds wide — and
    widest exactly when the intake is struggling. A duplicate arriving in that
    window must NOT be told the team has been notified: here the only send
    that ever runs fails, so nobody was told at all.
    """
    import asyncio

    fn = _feedback_tool()[1]
    in_flight = asyncio.Event()
    may_finish = asyncio.Event()

    async def _slow_failing_send(_payload):
        in_flight.set()
        await may_finish.wait()
        return False

    first = asyncio.create_task(_call(
        clean_dedup, monkeypatch, fn,
        send=_slow_failing_send, dedup_key="narra_cli:agent-token-invalid",
    ))
    await asyncio.wait_for(in_flight.wait(), timeout=5)

    second, also_sent = await _call(
        clean_dedup, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid",
    )
    # Suppressed (that is what reserving before the send buys) …
    assert also_sent == []
    # … but truthfully: the outcome is not known yet.
    assert second["notified"] is False
    assert "not known yet" in second["message"]
    assert "You may tell the user" not in second["message"]
    # This is the message a model sees over and over during an outage. Left as
    # an open question it invites the one workaround that defeats the gate.
    assert "Nothing for you to retry" in second["message"]
    assert "do not re-file it under a different dedup_key" in second["message"]

    may_finish.set()
    (first_result, _) = await first
    assert first_result["notified"] is False
    # The failed send released its claim, so the next call retries.
    assert not clean_dedup._feedback_dedup


@pytest.mark.asyncio
async def test_a_duplicate_after_a_confirmed_send_may_still_claim_it(
    clean_dedup, monkeypatch
):
    # The other side of the same branch: once a send is CONFIRMED delivered,
    # a later duplicate legitimately reports notified.
    fn = _feedback_tool()[1]
    await _call(clean_dedup, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid")
    slot = clean_dedup._dedup_slot("agent_a", "narra_cli:agent-token-invalid")
    assert clean_dedup._feedback_dedup[slot][1] is True  # delivered flag flipped

    second, _ = await _call(
        clean_dedup, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid",
    )
    assert second["notified"] is True
    assert "Already reported" in second["message"]


def test_confirming_a_delivery_does_not_disturb_expiry_order(clean_dedup):
    # The flag is flipped in place precisely so the key keeps its insertion
    # position — the front sweep reads insertion order AS time order.
    mt = clean_dedup
    first = mt._dedup_slot("agent_a", "tool:first")
    second = mt._dedup_slot("agent_a", "tool:second")
    _, rec = mt._dedup_reserve(first)
    mt._dedup_reserve(second)
    mt._dedup_confirm(first, rec)
    assert list(mt._feedback_dedup) == [first, second]


def test_a_late_failure_cannot_delete_someone_elses_reservation(clean_dedup):
    # Eviction can recycle a slot while our send is still in flight; releasing
    # by key alone would then drop the live reservation that replaced ours.
    mt = clean_dedup
    slot = mt._dedup_slot("agent_a", "tool:code")
    _, ours = mt._dedup_reserve(slot)
    del mt._feedback_dedup[slot]                    # evicted under pressure
    _, theirs = mt._dedup_reserve(slot)             # someone else re-claims it
    mt._dedup_confirm(slot, theirs)

    mt._dedup_release(slot, ours)                   # our send finally fails
    assert mt._feedback_dedup.get(slot) is theirs


@pytest.mark.asyncio
async def test_a_deployment_with_feedback_switched_off_says_so(
    clean_dedup, monkeypatch
):
    # send_feedback also returns False when NARRANEXUS_FEEDBACK_DISABLED=1, and
    # "could not reach them just now" would imply a transient outage forever.
    monkeypatch.setenv("NARRANEXUS_FEEDBACK_DISABLED", "1")
    result, sent = await _call(
        clean_dedup, monkeypatch, _feedback_tool()[1],
        dedup_key="narra_cli:agent-token-invalid",
    )
    assert sent == []
    assert result["ok"] is True and result["notified"] is False
    assert "switched off for this deployment" in result["message"]
    # A disabled deployment must not churn the cache either.
    assert not clean_dedup._feedback_dedup


@pytest.mark.asyncio
async def test_an_unknown_category_is_normalised_once_for_both_halves(
    clean_dedup, monkeypatch
):
    # feedback_client coerces an unknown category to "other" before posting;
    # without normalising here too, the tool would file one thing and describe
    # another.
    result, sent = await _call(
        clean_dedup, monkeypatch, _feedback_tool()[1], category="Error",
    )
    assert sent[0]["category"] == "other"
    assert result["notified"] is True
    assert "You may tell the user" not in result["message"]


def test_both_halves_of_the_slot_are_length_bounded(clean_dedup):
    # agent_id is model-supplied too; bounding only the key leaves 4096 entries
    # times an unbounded string.
    mt = clean_dedup
    slot = mt._dedup_slot("a" * 10_000, "k" * 10_000)
    assert len(slot[0]) == mt.FEEDBACK_DEDUP_KEY_MAXLEN
    assert len(slot[1]) == mt.FEEDBACK_DEDUP_KEY_MAXLEN


@pytest.mark.asyncio
async def test_a_cancelled_send_settles_its_slot_instead_of_muting_the_code(
    clean_dedup, monkeypatch
):
    """The exit path neither reserve-before-send nor release-on-failure covers.

    `send_feedback` catches `Exception`, which has not included
    `asyncio.CancelledError` since 3.8. Without a `finally`, cancelling the
    tool call mid-send leaves the slot `[stamp, False]` until the 6 h TTL: every
    later call for that agent + code is suppressed with no POST ever made, and
    told "a report is already on its way" when nothing is. That converts a
    reportable outage into an unreportable one, failing closed in the worst
    direction and looking like the feature working in the logs.
    """
    import asyncio

    fn = _feedback_tool()[1]
    in_flight = asyncio.Event()

    async def _never_returns(_payload):
        in_flight.set()
        await asyncio.Event().wait()   # cancelled from outside
        return True                     # pragma: no cover

    task = asyncio.create_task(_call(
        clean_dedup, monkeypatch, fn,
        send=_never_returns, dedup_key="narra_cli:agent-token-invalid",
    ))
    await asyncio.wait_for(in_flight.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not clean_dedup._feedback_dedup
    # The cache being empty is not enough on its own — confirm the next call
    # actually reaches the intake.
    _, sent = await _call(
        clean_dedup, monkeypatch, fn, dedup_key="narra_cli:agent-token-invalid",
    )
    assert len(sent) == 1


@pytest.mark.asyncio
async def test_a_raising_send_settles_its_slot_too(clean_dedup, monkeypatch):
    # Same exit path, non-cancellation flavour: anything escaping
    # send_feedback must not leave the slot claimed.
    fn = _feedback_tool()[1]

    async def _boom(_payload):
        raise RuntimeError("intake exploded")

    with pytest.raises(RuntimeError):
        await _call(
            clean_dedup, monkeypatch, fn,
            send=_boom, dedup_key="narra_cli:agent-token-invalid",
        )
    assert not clean_dedup._feedback_dedup


@pytest.mark.asyncio
async def test_an_empty_summary_is_not_reported_as_an_unreachable_team(
    clean_dedup, monkeypatch
):
    # send_feedback drops a blank summary before any network call and returns
    # False; describing that as "could not reach the team" would be untrue, and
    # burning a dedup slot on it would mute the real report.
    result, sent = await _call(
        clean_dedup, monkeypatch, _feedback_tool()[1],
        summary_override="   ", dedup_key="narra_cli:agent-token-invalid",
    )
    assert sent == []
    assert result["ok"] is True and result["notified"] is False
    assert "`summary` was empty" in result["message"]
    assert "Could not reach" not in result["message"]
    assert not clean_dedup._feedback_dedup


def test_only_the_owning_caller_gets_a_settleable_record(clean_dedup):
    # Handing the existing record to a non-owner would let it confirm or
    # release someone else's reservation.
    mt = clean_dedup
    slot = mt._dedup_slot("agent_a", "tool:code")
    state, record = mt._dedup_reserve(slot)
    assert state == mt.DEDUP_NEW and record is not None

    state, record = mt._dedup_reserve(slot)
    assert state == mt.DEDUP_PENDING and record is None
