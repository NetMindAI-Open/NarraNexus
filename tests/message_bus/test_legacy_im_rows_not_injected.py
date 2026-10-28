"""
@file_name: test_legacy_im_rows_not_injected.py
@author:
@date: 2026-08-18
@description: The defect the inbox rework is justified by, on the LIVE database.

Until 2026-08-17 `ChannelInboxWriter` mirrored every IM turn into `bus_messages`
so the Inbox could display it, under a channel nobody ever marked read. The
consequence, measured on prod: 1,364 messages permanently unread, riding into 90
agents' context every single turn, attributed to pseudo-agents like
`lark_user_<id>`.

Moving the inbox to its own tables stops NEW rows. It does nothing about the rows
already on every deployed database — and `_unread_predicate` is what hands them to
the model. So without this filter the rework would ship, the schema comment would
say the containment is "structural", and 90 agents would keep getting the same
poisoned context afterwards. The purge is a manual post-deploy step (the owner's
call, see the backfill runbook); the injection had to stop on the deploy.

Asserted through the three real readers, not through the predicate string: they
share `_unread_predicate` precisely so they cannot disagree, and "N unread (showing
M)" lying about its own list is the failure a divergence produces.
"""
from __future__ import annotations

import pytest

from narranexus.platform.channel.message_source_handler import im_channel_prefixes
from narranexus.platform.message_bus.local_bus import LocalMessageBus

AGENT = "agent_me"
PEER_CH = "ch_peer"


async def _seed_channel(db, channel_id: str, member: str):
    await db.insert("bus_channels", {
        "channel_id": channel_id, "name": channel_id,
        "channel_type": "direct", "created_by": member,
    })
    await db.insert("bus_channel_members", {
        "channel_id": channel_id, "agent_id": member,
    })


@pytest.mark.asyncio
async def test_a_legacy_im_row_is_not_unread_for_any_reader(db_client):
    """One legacy row and one real peer message; only the peer message counts."""
    bus = LocalMessageBus(backend=db_client._backend)

    prefixes = im_channel_prefixes()
    assert prefixes, "no dedicated-trigger channels registered — fixture is void"
    legacy_ch = f"{prefixes[0]}oc_legacy_chat"

    await _seed_channel(db_client, legacy_ch, AGENT)
    await _seed_channel(db_client, PEER_CH, AGENT)

    # The shape the old writer left: a pseudo-agent sender, never marked read.
    await bus.send_message(
        from_agent="lark_user_12345", to_channel=legacy_ch, content="旧的 IM 历史",
    )
    await bus.send_message(
        from_agent="agent_peer", to_channel=PEER_CH, content="a real peer message",
    )

    unread = await bus.get_unread(AGENT)
    contents = [m.content for m in unread]
    assert contents == ["a real peer message"], (
        f"a legacy IM row reached the agent's context: {contents}"
    )

    # All three readers, because they share the predicate so that they agree —
    # a count that disagrees with its own list is how "N unread (showing M)"
    # starts lying.
    assert await bus.count_unread(AGENT) == 1
    assert await bus.has_unread_before(AGENT, PEER_CH, "2999-01-01") is True
    # And the legacy channel answers False even asked directly about it.
    assert await bus.has_unread_before(AGENT, legacy_ch, "2999-01-01") is False

    # And with ONLY the legacy row present, every reader says "nothing".
    await db_client.delete("bus_messages", {"channel_id": PEER_CH})
    assert await bus.get_unread(AGENT) == []
    assert await bus.count_unread(AGENT) == 0
    assert await bus.has_unread_before(AGENT, PEER_CH, "2999-01-01") is False


@pytest.mark.asyncio
async def test_every_dedicated_channel_prefix_is_excluded_not_just_the_first(
    db_client,
):
    """The filter is built from the whole registry, one clause per prefix.

    The hand-maintained version of this list drifted and cost the 2026-07-03
    wechat incident; a filter that only covered `prefixes[0]` would reproduce
    exactly that, and the test above would not notice.
    """
    bus = LocalMessageBus(backend=db_client._backend)

    for i, prefix in enumerate(im_channel_prefixes()):
        cid = f"{prefix}chat_{i}"
        await _seed_channel(db_client, cid, AGENT)
        await bus.send_message(
            from_agent=f"{prefix}user_{i}", to_channel=cid, content=f"legacy {i}",
        )

    assert await bus.get_unread(AGENT) == []
    assert await bus.count_unread(AGENT) == 0


@pytest.mark.asyncio
async def test_the_prefix_match_is_not_a_like_wildcard(db_client):
    """`_` is a single-character LIKE wildcard, and every prefix ends in one.

    The first version of this filter used `NOT LIKE 'lark_%'`, which also excludes
    `larkX_…` and `larky_…` — any channel whose id starts with "lark" plus any one
    character. Verified on SQLite at the time: with ids
    (lark_oc_1, larkX_oc_2, larky_room, ch_team_1, lark) the unescaped pattern kept
    only (ch_team_1, lark).

    Current id formats make the over-match unreachable, which is precisely why it
    would have survived until someone changed an id format — and this filter
    decides what reaches the model, so an over-match is silent context loss rather
    than an error. `SUBSTR(...) <> ?` has no wildcard semantics and lets the prefix
    be a bound parameter.
    """
    bus = LocalMessageBus(backend=db_client._backend)

    prefixes = im_channel_prefixes()
    prefix = prefixes[0]                    # e.g. "lark_"
    stem = prefix.rstrip("_")               # "lark"
    assert prefix.endswith("_"), f"fixture assumes a trailing underscore: {prefix}"

    legacy = f"{prefix}oc_1"                # must be excluded
    lookalikes = [f"{stem}X_oc_2", f"{stem}y_room"]  # must NOT be excluded

    for cid in (legacy, *lookalikes):
        await _seed_channel(db_client, cid, AGENT)
        await bus.send_message(
            from_agent="someone_else", to_channel=cid, content=f"from {cid}",
        )

    got = {m.content for m in await bus.get_unread(AGENT)}
    assert got == {f"from {c}" for c in lookalikes}, (
        f"the prefix match is behaving as a wildcard: kept {got}"
    )
    assert await bus.count_unread(AGENT) == len(lookalikes)


def test_the_predicate_and_its_params_cannot_desynchronise(db_client):
    """Placeholder count == parameter count, structurally, not by convention.

    These were two methods reading `im_channel_prefixes()` separately, with nothing
    tying the number of emitted placeholders to the length of the returned tuple.
    Merging them into one method was not sufficient either: the SQL came from a
    generator and the params from `tuple(prefixes)`, so a condition added to the
    generator left the params unfiltered. Mutation-verified at the time — adding
    `if len(pfx) > 6` to the generator passed the whole suite.

    The failure that shift produces is the bad kind. `has_unread_before` is the one
    caller that INTERLEAVES its parameters, so one extra param means
    `m.channel_id = ?` receives a prefix string and `m.created_at < ?` receives a
    channel id. The query matches nothing, `has_unread_before` returns False, the
    caller skips its early return, and `ack_read` advances the cursor — discarding
    every unread message older than the rendered window. No exception, and no test
    failure unless a fixture happens to hold the right number of prefixes.

    Clause and param are appended together in one loop now, so a `continue` drops
    both. This test states the invariant the loop provides.
    """
    from narranexus.platform.message_bus.local_bus import LocalMessageBus

    bus = LocalMessageBus(backend=db_client._backend)
    for ph in ("?", "%s"):
        sql, params = bus._unread_predicate(ph)
        emitted = sql.count("SUBSTR(m.channel_id")
        assert emitted == len(params), (
            f"{emitted} prefix placeholders vs {len(params)} params with ph={ph!r} "
            f"— every later parameter in `has_unread_before` is shifted"
        )
        # And the predicate's own two placeholders are still there, so the count
        # above is not accidentally matching an empty prefix list.
        assert params, "no prefixes at all — the registry is empty in this fixture"
        assert sql.count(ph) == 2 + len(params)
