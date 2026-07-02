"""
@file_name: test_narramessenger_trigger.py
@date: 2026-07-02
@description: Tests for NarramessengerTrigger owner auto-claim (X2/X3 fix).

Why this file exists:
    ``do_bind`` (``_narramessenger_service.py``) never learns the binder's
    Matrix identity — the connect response only returns the AGENT's own
    matrixUserId/principalId/roomId. That left ``owner_matrix_user_id``
    permanently empty after a bind, which made ``NarramessengerModule``
    (a) always compute ``is_owner_interacting=False`` (the owner looked
    like a visitor — X2) and (b) render "No owner is registered" in the
    trust block (X3). The fix claims the first sender in the bind room
    (``credential.bind_room_id``) as owner — mirrors Telegram's
    ``_maybe_resolve_owner`` late-binding pattern, adapted because
    NarraMessenger has no username lock to carry from bind time.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
    NarramessengerCredentialManager,
)
from xyz_agent_context.module.narramessenger_module.narramessenger_module import (
    NarramessengerModule,
)
from xyz_agent_context.module.narramessenger_module.narramessenger_trigger import (
    NarramessengerTrigger,
)
from xyz_agent_context.schema import ContextData
from xyz_agent_context.schema.parsed_message import ChatType, ParsedMessage


def _cred_bound(
    bind_room_id: str = "!bindroom:matrix.netmind.chat",
    owner_matrix_user_id: str = "",
    owner_name: str = "",
) -> NarramessengerCredential:
    """A credential as it looks right after ``do_bind`` — agent identity is
    populated, owner identity is not (see module docstring)."""
    return NarramessengerCredential(
        agent_id="agent_a",
        bearer_token="tok-123",
        backend_base_url="https://api.netmind.chat",
        matrix_user_id="@agent-e7726996:matrix.netmind.chat",
        nexus_principal_id="principal-1",
        bind_room_id=bind_room_id,
        owner_matrix_user_id=owner_matrix_user_id,
        owner_name=owner_name,
        connection_mode="gateway",
        enabled=True,
    )


def _dm(room_id: str, sender_id: str, sender_name: str = "") -> ParsedMessage:
    """A DM ParsedMessage as ``NarramessengerTrigger.parse_event`` would emit."""
    return ParsedMessage(
        message_id="inv-1",
        chat_id=room_id,
        sender_id=sender_id,
        sender_name=sender_name or sender_id,
        content="hi",
        chat_type=ChatType.PRIVATE,
        timestamp_ms=0,
        raw={"invocation_id": "inv-1", "room_id": room_id},
    )


# ── _maybe_claim_owner ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_claim_owner_fires_on_first_bind_room_message(db_client):
    """First DM in the bind room claims the sender as owner: persisted +
    reflected in-memory on the credential object (so THIS turn already
    sees it)."""
    mgr = NarramessengerCredentialManager(db_client)
    cred = _cred_bound()
    await mgr.upsert(cred)

    trigger = NarramessengerTrigger()
    trigger._db = db_client

    message = _dm(
        room_id="!bindroom:matrix.netmind.chat",
        sender_id="@zehua:matrix.netmind.chat",
        sender_name="Zehua",
    )

    await trigger._maybe_claim_owner(cred, message)

    # In-memory mutation — visible this turn.
    assert cred.owner_matrix_user_id == "@zehua:matrix.netmind.chat"
    assert cred.owner_name == "Zehua"

    # Persisted.
    after = await mgr.get("agent_a")
    assert after is not None
    assert after.owner_matrix_user_id == "@zehua:matrix.netmind.chat"
    assert after.owner_name == "Zehua"


@pytest.mark.asyncio
async def test_process_message_claims_owner_only_for_bind_room(db_client):
    """SECURITY: a message from a room OTHER than the bind room must NOT
    claim ownership — only the room the binder themselves created."""
    mgr = NarramessengerCredentialManager(db_client)
    cred = _cred_bound(bind_room_id="!bindroom:matrix.netmind.chat")
    await mgr.upsert(cred)

    trigger = NarramessengerTrigger()
    trigger._db = db_client

    # Message from a DIFFERENT room (e.g. a group the agent was added to).
    message = _dm(
        room_id="!some-other-room:matrix.netmind.chat",
        sender_id="@stranger:matrix.netmind.chat",
    )

    await trigger._maybe_claim_owner(cred, message)

    # No-op — wrong room can't claim.
    assert cred.owner_matrix_user_id == ""
    after = await mgr.get("agent_a")
    assert after.owner_matrix_user_id == ""


@pytest.mark.asyncio
async def test_claim_owner_does_not_overwrite_existing_owner(db_client):
    """Once an owner is registered, later bind-room messages from a
    different sender must NOT reassign ownership."""
    mgr = NarramessengerCredentialManager(db_client)
    cred = _cred_bound(
        owner_matrix_user_id="@zehua:matrix.netmind.chat",
        owner_name="Zehua",
    )
    await mgr.upsert(cred)

    trigger = NarramessengerTrigger()
    trigger._db = db_client

    # A later bind-room message from a DIFFERENT sender must not reassign
    # ownership — owner_matrix_user_id is already set.
    later_message = _dm(
        room_id=cred.bind_room_id,
        sender_id="@someone-else:matrix.netmind.chat",
    )
    await trigger._maybe_claim_owner(cred, later_message)

    assert cred.owner_matrix_user_id == "@zehua:matrix.netmind.chat"
    after = await mgr.get("agent_a")
    assert after.owner_matrix_user_id == "@zehua:matrix.netmind.chat"


@pytest.mark.asyncio
async def test_claim_owner_is_noop_without_bind_room_id(db_client):
    """A credential with no bind_room_id recorded (defensive: shouldn't
    happen post-bind, but must not crash or wrongly claim) never fires."""
    cred = _cred_bound(bind_room_id="")
    trigger = NarramessengerTrigger()
    trigger._db = db_client

    message = _dm(room_id="!anyroom:matrix.netmind.chat", sender_id="@x:matrix.netmind.chat")

    await trigger._maybe_claim_owner(cred, message)

    assert cred.owner_matrix_user_id == ""


# ── SECURITY: agent self-echo must never be claimed as owner ────────────


@pytest.mark.asyncio
async def test_claim_owner_ignores_agents_own_echoed_message(db_client):
    """If the platform ever echoes the agent's OWN outbound message back
    into the bind room (sender_id == credential.matrix_user_id), that must
    NEVER be claimed as owner — otherwise the agent could permanently lock
    itself in as its own owner. ``_maybe_claim_owner`` runs BEFORE the base
    class's ``is_echo`` filter (see ``_process_message`` override), so the
    self-sender exclusion has to live in the claim gate itself, not rely on
    the base pipeline having already filtered echoes out."""
    mgr = NarramessengerCredentialManager(db_client)
    cred = _cred_bound()  # matrix_user_id = "@agent-e7726996:matrix.netmind.chat"
    await mgr.upsert(cred)

    trigger = NarramessengerTrigger()
    trigger._db = db_client

    echoed_own_message = _dm(
        room_id=cred.bind_room_id,
        sender_id=cred.matrix_user_id,  # the agent's own identity
        sender_name="Agent",
    )

    await trigger._maybe_claim_owner(cred, echoed_own_message)

    assert cred.owner_matrix_user_id == ""
    after = await mgr.get("agent_a")
    assert after.owner_matrix_user_id == ""


@pytest.mark.asyncio
async def test_process_message_real_pipeline_never_claims_agent_echo(db_client):
    """Exercises the REAL ``_process_message`` call order (not just
    ``_maybe_claim_owner`` in isolation): register the credential as an
    active subscriber so the base pipeline reaches its own ``is_echo``
    filter, then feed it a message from the agent's own matrix_user_id in
    the bind room. Both the owner-claim gate AND the base echo filter must
    independently refuse to treat the agent as its own owner."""
    mgr = NarramessengerCredentialManager(db_client)
    cred = _cred_bound()
    await mgr.upsert(cred)

    trigger = NarramessengerTrigger()
    trigger._db = db_client
    trigger._subscriber_creds[cred.agent_id] = cred

    echoed_own_message = _dm(
        room_id=cred.bind_room_id,
        sender_id=cred.matrix_user_id,
        sender_name="Agent",
    )

    await trigger._process_message(cred, echoed_own_message)

    assert cred.owner_matrix_user_id == ""
    after = await mgr.get("agent_a")
    assert after.owner_matrix_user_id == ""


# ── End-to-end: claim → build_extra_data (X2/X3 regression) ─────────────


@pytest.mark.asyncio
async def test_owner_claim_makes_module_recognize_owner_same_turn(db_client):
    """Regression test for X2 + X3: after the bind-room first-DM claim,
    ``NarramessengerModule.build_extra_data`` (fed by the SAME credential
    object mutated in-memory) must report ``is_owner_interacting=True``
    for that sender — not treat the owner as a visitor."""
    mgr = NarramessengerCredentialManager(db_client)
    cred = _cred_bound()
    await mgr.upsert(cred)

    trigger = NarramessengerTrigger()
    trigger._db = db_client

    message = _dm(
        room_id=cred.bind_room_id,
        sender_id="@zehua:matrix.netmind.chat",
        sender_name="Zehua",
    )
    await trigger._maybe_claim_owner(cred, message)

    module = NarramessengerModule(agent_id="agent_a", user_id=None, database_client=db_client)
    ctx = ContextData(
        agent_id="agent_a",
        input_content="hi",
        extra_data={"channel_tag": {"sender_id": "@zehua:matrix.netmind.chat"}},
    )
    extra = await module.build_extra_data(cred, ctx)

    assert extra["is_owner_interacting"] is True
    assert extra["owner_matrix_user_id"] == "@zehua:matrix.netmind.chat"

    instructions = module._trust_block(extra)
    assert "No owner is registered" not in instructions
    assert "is_owner_interacting=True" in instructions
