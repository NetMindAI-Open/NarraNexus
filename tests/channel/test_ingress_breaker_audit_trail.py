"""
@file_name: test_ingress_breaker_audit_trail.py
@author:
@date: 2026-08-28
@description: Binding rule #16 gate — a suppressed message leaves a row.

Once the breaker is wired, a message judged part of a storm is dropped and
the far side sees nothing: the assistant simply stops answering. That is
the 0802 symptom, and the 8/14 incident ran 70+ hours precisely because
"the bot went quiet" was not answerable from any durable record — the logs
had rotated and there was nothing in the DB to count.

So the rule for this path is: every drop writes a row, per message, and the
row carries enough to answer "why, and until when". The managed surface has
the same gate in `tests/backend/test_manyfold_im_ingress.py`; this file
covers the native one, which audits through a different call site — a fact
that only surfaced because a mutation removing the native audit left the
managed tests green.
"""
from __future__ import annotations

import pytest

from narranexus.platform.channel.channel_audit_events import (
    EVENT_INGRESS_BREAKER_CLEARED,
    EVENT_INGRESS_BREAKER_TRIPPED,
    EVENT_INGRESS_DROPPED_BREAKER,
)
from narranexus.platform.channel.channel_trigger_base import ChannelTriggerBase
from narranexus.platform.repository.channel_trigger_audit_repository import (
    ChannelTriggerAuditRepository,
)
from narranexus.platform.schema.parsed_message import ChatType, ParsedMessage
from narranexus.platform.schema.hook_schema import WorkingSource

pytestmark = pytest.mark.asyncio


class _Cred:
    app_id = "app_1"
    agent_id = "agent_a"
    user_id = "user_owner"


class _StormTrigger(ChannelTriggerBase):
    """A real subclass, so the guard is built from its own class attributes
    and this also pins that the knobs are the ones the base declares."""

    channel_name = "fake_breaker"
    brand_display = "Fake"
    working_source = WorkingSource.LARK

    INGRESS_RATE_THRESHOLD = 5
    INGRESS_DUP_RATIO_THRESHOLD = 0.5

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.ran = 0

    async def load_active_credentials(self):  # pragma: no cover
        return []

    async def connect(self, credential):  # pragma: no cover
        yield {}

    def parse_event(self, raw):
        return ParsedMessage(
            message_id=raw["id"],
            chat_id="room_1",
            chat_type=ChatType.PRIVATE,
            sender_id="peer_1",
            sender_name="Peer",
            content=raw["content"],
            timestamp_ms=raw["ts_ms"],
        )

    async def is_echo(self, message, credential):
        return False

    async def resolve_sender_name(self, sender_id, credential):
        return sender_id

    def create_context_builder(self, message, credential, agent_id):  # pragma: no cover
        return None

    async def _build_and_run_agent(self, *a, **kw):
        """Short-circuited: this file is about what happens to the messages
        the breaker REFUSES. Letting the admitted ones build a real run
        would drag the whole context pipeline into a test whose subject is
        one audit row."""
        self.ran += 1
        return ""


async def _storm(db_client, n: int):
    """Feed n identical messages through the real `_process_message`."""
    trigger = _StormTrigger(base_workers=1)
    trigger._audit_repo = ChannelTriggerAuditRepository(
        _StormTrigger.channel_name, db_client
    )
    cred = _Cred()
    trigger._subscriber_creds[cred.app_id] = cred
    # `start()` is what builds the guard in production; this test drives
    # `_process_message` directly, so build it from the same factory rather
    # than hand-rolling one — otherwise the test would be pinning a guard
    # configured differently from the one that ships.
    trigger._ingress_guard = trigger.build_ingress_guard(db_client)
    for i in range(n):
        msg = trigger.parse_event(
            {"id": f"m{i}", "content": "same thing again", "ts_ms": 1000 + i}
        )
        await trigger._process_message(cred, msg)
    return trigger


async def _rows(db_client, event_type):
    return await db_client.get(
        "channel_trigger_audit",
        {"channel": _StormTrigger.channel_name, "event_type": event_type},
    )


async def test_every_message_the_breaker_drops_leaves_its_own_row(db_client):
    """Per message, not per trip.

    One row per trip would answer "it closed" but not "for how long, and
    how much did it swallow" — and a silent return answers neither, which
    is the blind spot that let the original incident run unnoticed.
    """
    await _storm(db_client, 12)

    trips = await _rows(db_client, EVENT_INGRESS_BREAKER_TRIPPED)
    drops = await _rows(db_client, EVENT_INGRESS_DROPPED_BREAKER)

    assert trips, "the moment the door closed is not in the DB"
    assert drops, "messages were suppressed with no trace at all"
    # Every message after the trip gets its own row, and each row names the
    # message it silenced — otherwise "which of my messages got through?"
    # is unanswerable.
    ids = [r["message_id"] for r in drops]
    assert len(ids) == len(set(ids)), f"rows are not per message: {ids}"


async def test_a_native_drop_row_says_why_and_until_when(db_client):
    import json

    await _storm(db_client, 12)
    drops = await _rows(db_client, EVENT_INGRESS_DROPPED_BREAKER)
    assert drops

    details = drops[0]["details"]
    if isinstance(details, str):
        details = json.loads(details)

    for field in (
        "session_key", "tier", "reason", "suppressed",
        "cooldown_remaining_seconds",
    ):
        assert field in details, f"{field} missing from {sorted(details)}"
    assert details["reason"] == "cooling"
    assert details["tier"] >= 1
    assert details["cooldown_remaining_seconds"] > 0, (
        "a row that cannot say how much longer leaves 'until when' "
        "unanswerable, which is half of what these rows are for"
    )


# ---------------------------------------------------------------------------
# One mapping, two writers
#
# The WRITE genuinely has to happen twice — the native gate goes through
# `_audit`, the managed coordinator through its own direct seam, and a
# mutation removing either one leaves the other's tests green. But "which
# event is this transition" is knowledge, not plumbing, and it used to be
# copied verbatim into both. `IngressVerdict.audit_event()` owns it now.
#
# The design still has an observe tier and a merge tier unbuilt, so
# `transition` will grow values. A copy that does not grow with it means
# those events simply never exist for one of the two surfaces — and that
# surface is the one the audit trail is supposed to explain.
# ---------------------------------------------------------------------------

_TRANSITIONS = [None, "tripped", "escalated", "probe", "recovered"]


@pytest.mark.parametrize("transition", _TRANSITIONS)
@pytest.mark.parametrize("admit", [True, False])
def test_the_verdict_owns_the_event_mapping(transition, admit):
    """The mapping itself, checked on the verdict.

    Deliberately NOT named "both surfaces": this constructs a verdict and
    calls `audit_event()`, it does not run either receive path. What
    guarantees the two agree is the source-level check below — a name
    claiming end-to-end coverage it does not have is worse than no name.
    """
    from narranexus.platform.channel.ingress_guard import IngressVerdict

    verdict = IngressVerdict(
        admit=admit,
        session_key="a|c|r|s",
        tier=1,
        reason="cooling" if not admit else "ok",
        transition=transition,
    )
    event = verdict.audit_event()

    if transition in ("tripped", "escalated"):
        assert event == EVENT_INGRESS_BREAKER_TRIPPED
    elif transition in ("probe", "recovered"):
        assert event == EVENT_INGRESS_BREAKER_CLEARED
    elif not admit:
        assert event == EVENT_INGRESS_DROPPED_BREAKER
    else:
        assert event is None, "a plain admitted turn is already ingress_processed"


def test_neither_call_site_keeps_its_own_copy_of_the_mapping():
    """The two writers must ask the verdict, not decide for themselves.

    Scoped to the two functions that write, not to whole modules: a
    `/healthz` counter or a docstring naming `ingress_breaker_tripped`
    elsewhere in either file is perfectly legitimate, and failing on it
    would report "the mapping is drifting" about a change that has nothing
    to do with the mapping.
    """
    import inspect

    from narranexus.platform.channel.channel_trigger_base import ChannelTriggerBase
    from narranexus.platform.module_system.managed_channel_ingress import (
        ManagedChannelIngress,
    )

    writers = {
        "ChannelTriggerBase._audit_ingress_verdict": (
            ChannelTriggerBase._audit_ingress_verdict
        ),
        "ManagedChannelIngress._ingress_admitted": (
            ManagedChannelIngress._ingress_admitted
        ),
    }
    for name, fn in writers.items():
        src = inspect.getsource(fn)
        assert "verdict.audit_event()" in src, (
            f"{name} does not ask the verdict which event this is"
        )
        for const in (
            "EVENT_INGRESS_BREAKER_TRIPPED",
            "EVENT_INGRESS_BREAKER_CLEARED",
            "EVENT_INGRESS_DROPPED_BREAKER",
        ):
            assert const not in src, (
                f"{name} names {const} directly — the mapping is drifting "
                f"back into the call sites, and a new transition will reach "
                f"one surface and not the other"
            )


async def test_retention_only_sweeps_its_own_channel(db_client):
    """The retention window is a per-trigger class attribute, so the sweep
    has to be per-channel too.

    Unscoped, `INGRESS_BREAKER_RETENTION_DAYS` does not mean what its
    declaration says: six native triggers in one process would each sweep
    the whole table, and whichever ticks first decides everyone's window.
    A channel that widened its window to 90 days for an incident review
    would still lose its rows on day 30 — no error, no warning, discovered
    only when someone goes looking. The two sweeps beside it in
    `_run_cleanup` are both scoped for exactly this reason.
    """
    from narranexus.platform.repository import ChannelIngressBreakerRepository
    from narranexus.platform.utils.timezone import utc_now
    from datetime import timedelta

    repo = ChannelIngressBreakerRepository(db_client)
    old = utc_now() - timedelta(days=90)
    for channel in ("mine", "someone_else"):
        await repo.upsert_state(
            f"a1|{channel}|room|peer",
            {
                "channel": channel,
                "agent_id": "a1",
                "chat_id": "room",
                "sender_id": "peer",
                "tier": 0,
                "cooldown_until": None,
            },
        )
        await db_client.update(
            "channel_ingress_breaker",
            {"session_key": f"a1|{channel}|room|peer"},
            {"updated_at": old.isoformat()},
        )

    deleted = await repo.cleanup_older_than_days(30, channel="mine")

    assert deleted == 1, f"swept {deleted} rows — the scope is not applied"
    survivors = await db_client.get("channel_ingress_breaker", {})
    channels = {r["channel"] for r in survivors}
    assert channels == {"someone_else"}, (
        f"another channel's rows were deleted by this channel's tick: "
        f"{channels}"
    )


def test_every_transition_the_guard_produces_maps_to_an_event():
    """Adding a transition without mapping it must go red HERE.

    Collapsing the mapping into one place moved the drift point rather
    than removing it: the new risk is a value that reaches neither face.
    `audit_event()` returns None for it and both writers skip the row, so
    the failure is "the DB has no record of it" — discovered by whoever
    goes looking during an incident.

    `admit=True` on purpose: with `admit=False` the "not admitted" branch
    would hand back a drop event for any unmapped value and this assertion
    would be vacuously true.
    """
    from narranexus.platform.channel.ingress_guard import (
        INGRESS_TRANSITIONS,
        IngressVerdict,
    )

    unmapped = [
        t
        for t in sorted(INGRESS_TRANSITIONS)
        if IngressVerdict(
            admit=True, session_key="a|c|r|s", tier=1, reason="ok", transition=t
        ).audit_event()
        is None
    ]
    assert not unmapped, (
        f"these transitions write no audit row on either receive face: "
        f"{unmapped}"
    )
