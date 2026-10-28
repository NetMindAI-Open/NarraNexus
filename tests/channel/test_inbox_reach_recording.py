"""
@file_name: test_inbox_reach_recording.py
@author:
@date: 2026-08-21
@description: Reachability is recorded AUTOMATICALLY on an inbound channel turn.

Before this, `contact_info.channels` was populated only if the model remembered
to call `extract_entity_info` (channel prompt instruction #5) — so the address
book was effectively empty, and an agent on another surface had no way to learn
it could reach a contact on Lark/Slack/etc.

`InboxRecorder.record_turn` fires on every inbound channel turn and knows the
channel, the agent, the counterpart, and the conversation id. On a **1:1**
(``ChatType.PRIVATE``) turn it writes "agent reaches counterpart on <channel>
via <chat_id>" onto the counterpart's social entity — the single home for reach
(no parallel per-surface rosters). A group turn records nothing (a room is not a
way to reach one person). Best-effort: it must never break the inbox write.
"""
from __future__ import annotations

import pytest

from narranexus.platform.channel.channel_contact_utils import get_room_id
from narranexus.platform.channel.inbox_recorder import InboxRecorder, im_thread_id
from narranexus.platform.repository.social_network_repository import SocialNetworkRepository
from narranexus.platform.schema.parsed_message import ChatType

AGENT, COUNTERPART, CHAT = "agent_a", "U_alice", "C_room7"
INSTANCE = "social_test0001"


@pytest.fixture(autouse=True)
def _direct_store(db_client, monkeypatch):
    """DirectStore (no backend URL) over the in-memory db."""
    monkeypatch.delenv("NARRANEXUS_BACKEND_URL", raising=False)

    async def _get_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _get_db
    )


async def _seed_social_instance(db):
    await db.insert(
        "module_instances",
        {"instance_id": INSTANCE, "module_class": "SocialNetworkModule",
         "agent_id": AGENT, "is_public": 1},
    )


async def _record(db, *, channel="slack", chat_id=CHAT, counterpart=COUNTERPART,
                  chat_type=ChatType.PRIVATE, counterpart_name="Alice"):
    await InboxRecorder(channel, channel.title()).record_turn(
        db=db,
        thread_id=im_thread_id(channel, AGENT, chat_id),
        owner_user_id="usr_owner",
        agent_id=AGENT,
        counterpart_id=counterpart,
        counterpart_name=counterpart_name,
        inbound_text="hi",
        outbound_text="hello",
        chat_id=chat_id,
        chat_type=chat_type,
    )


@pytest.mark.asyncio
async def test_an_inbound_turn_records_how_to_reach_the_sender(db_client):
    """The whole point: after Alice writes on Slack, the agent's social graph
    knows it can reach Alice on Slack in conversation C_room7."""
    await _seed_social_instance(db_client)

    await _record(db_client)

    entity = await SocialNetworkRepository(db_client).get_entity(
        entity_id=COUNTERPART, instance_id=INSTANCE
    )
    assert entity is not None, "no social entity was written for the sender"
    assert get_room_id(entity.contact_info, "slack", AGENT) == CHAT


@pytest.mark.asyncio
async def test_reach_is_not_recorded_without_a_conversation_id(db_client):
    """No chat_id → nothing to point at → no reach write (and no crash)."""
    await _seed_social_instance(db_client)

    await _record(db_client, chat_id="")

    entity = await SocialNetworkRepository(db_client).get_entity(
        entity_id=COUNTERPART, instance_id=INSTANCE
    )
    assert entity is None, "a skipped reach must not create an entity at all"


@pytest.mark.asyncio
@pytest.mark.parametrize("group_type", [ChatType.GROUP, ChatType.TOPIC_GROUP])
async def test_a_group_turn_records_no_personal_reach(db_client, group_type):
    """CRITICAL guard: in a group, `chat_id` is the ROOM, not a way to reach the
    speaker. Recording it as "reach Alice here" and then sending a message for
    Alice to that id would post it to the whole group. A non-1:1 turn must record
    nothing. Removing the `chat_type == PRIVATE` gate turns this red."""
    await _seed_social_instance(db_client)

    await _record(db_client, chat_type=group_type)

    entity = await SocialNetworkRepository(db_client).get_entity(
        entity_id=COUNTERPART, instance_id=INSTANCE
    )
    assert entity is None, "a group turn must not write personal reach"


@pytest.mark.asyncio
async def test_first_contact_entity_gets_a_name(db_client):
    """§3b's first step searches by NAME, so a first-contact entity must not be
    nameless. The name is passed create-only (`entity_name_if_new`)."""
    await _seed_social_instance(db_client)

    await _record(db_client, counterpart_name="Alice Zhang")

    entity = await SocialNetworkRepository(db_client).get_entity(
        entity_id=COUNTERPART, instance_id=INSTANCE
    )
    assert entity is not None and entity.entity_name == "Alice Zhang"


@pytest.mark.asyncio
async def test_a_later_channel_name_never_overwrites_an_existing_name(db_client):
    """The create-only name must NOT clobber a canonical name set elsewhere (the
    merge branch drops `entity_name_if_new`). Seed a named entity, then record a
    reach turn carrying a different display name — the canonical name survives."""
    from narranexus.platform.module_system.data_access import get_agent_data_store

    await _seed_social_instance(db_client)
    await get_agent_data_store().extract_entity_info(
        agent_id=AGENT, entity_id=COUNTERPART,
        updates={"entity_name": "Dr. Alice Zhang"}, update_mode="merge",
    )

    await _record(db_client, counterpart_name="alice (slack display)")

    entity = await SocialNetworkRepository(db_client).get_entity(
        entity_id=COUNTERPART, instance_id=INSTANCE
    )
    assert entity.entity_name == "Dr. Alice Zhang"


@pytest.mark.asyncio
async def test_a_reach_turn_fills_an_existing_nameless_entity(db_client):
    """`entity_name_if_new` is fill-if-empty on merge, not create-only: an
    entity some other path made nameless (an LLM extraction that only had
    contact_info) gets named by a later 1:1 reach turn — otherwise §3b's
    name-search never finds it."""
    from narranexus.platform.module_system.data_access import get_agent_data_store

    await _seed_social_instance(db_client)
    # A nameless entity created elsewhere (contact_info only, no name).
    await get_agent_data_store().extract_entity_info(
        agent_id=AGENT, entity_id=COUNTERPART,
        updates={"contact_info": {"email": "a@x.com"}}, update_mode="merge",
    )

    await _record(db_client, counterpart_name="Alice Zhang")

    entity = await SocialNetworkRepository(db_client).get_entity(
        entity_id=COUNTERPART, instance_id=INSTANCE
    )
    assert entity.entity_name == "Alice Zhang"


@pytest.mark.asyncio
async def test_an_in_band_store_failure_is_logged_not_swallowed(db_client, monkeypatch):
    """The store reports failure IN-BAND (never raises), so the fail-open
    try/except alone would hide every real failure (no social instance, rejected
    id, post-P2 auth 401). _record_reach checks the return value and warns. Here:
    no social instance seeded → in-band failure → a warning fires, and the inbox
    row still exists. Removing the `res.get("success") is False` check → red."""
    import narranexus.platform.channel.inbox_recorder as ir

    warnings: list = []

    class _Spy:
        def warning(self, msg, *a, **k):
            warnings.append(msg)

        def __getattr__(self, _):
            return lambda *a, **k: None

    monkeypatch.setattr(ir, "logger", _Spy())

    # no _seed_social_instance → the store returns {"success": False, ...}
    await _record(db_client)

    thread = await db_client.get_one(
        "inbox_threads", {"thread_id": im_thread_id("slack", AGENT, CHAT)}
    )
    assert thread is not None
    assert any("reach not recorded" in m for m in warnings)


@pytest.mark.asyncio
async def test_reach_failure_never_breaks_the_inbox_write(db_client, monkeypatch):
    """Best-effort: if the reach write RAISES, the inbox turn is still recorded
    and no exception escapes `record_turn`.

    The store's own no-instance path returns an in-band error dict rather than
    raising, so an absent instance does NOT exercise the `try/except`. We force a
    real raise (an ImportError-shaped failure of the lazy `get_agent_data_store`
    import path is the realistic future regression) and prove the inbox row and
    the caller survive it. Delete `_record_reach`'s `try/except` → this goes red.
    """
    await _seed_social_instance(db_client)

    def _boom(*a, **k):
        raise RuntimeError("store exploded")

    # Patched at its source: `_record_reach` does `from ...data_access import
    # get_agent_data_store` at call time, so it re-reads this symbol.
    monkeypatch.setattr(
        "narranexus.platform.module_system.data_access.get_agent_data_store", _boom
    )

    await _record(db_client)  # must NOT raise

    thread = await db_client.get_one(
        "inbox_threads", {"thread_id": im_thread_id("slack", AGENT, CHAT)}
    )
    assert thread is not None, "the inbox write was lost to a reach failure"
