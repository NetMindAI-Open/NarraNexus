"""
@file_name: test_context_builder_no_inline_history.py
@date: 2026-07-09
@description: Lock the empty-payload contract for
``NarramessengerContextBuilder``'s ``get_conversation_history`` and
``get_room_members``.

Pre-Matrix (Gateway/polling) NarraMessenger delivered inline
``group_context.history_messages`` (group) / ``context`` (DM) plus
``group_context.members`` on every invocation, and these methods
normalised them for the base's ``## Conversation History`` and
``## Conversation Members`` slots.

Direct Matrix (Commit 7, 2026-07-02) reads raw ``m.room.message`` off
``/sync``; ``matrix_trigger._wrap_event`` produces none of those
fields in ``ParsedMessage.raw``. The pre-2026-07-09 implementation was
dead code — every call returned an empty list via the fallback branch.

The 2026-07-09 refactor made this explicit: both methods return ``[]``
unconditionally. History is served from ChatModule memory during
``hook_data_gathering``; live roster is fetched on demand via the
``narra_room_members`` MCP tool. Current-turn attachment markers are
injected at ``context_runtime.build_input_for_framework``; historical
markers use the same ``Attachment.markers_from_dicts`` helper —
identical shape at both callsites.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.module.narramessenger_module._narramessenger_credential_manager import (
    NarramessengerCredential,
)
from xyz_agent_context.module.narramessenger_module.narramessenger_context_builder import (
    NarramessengerContextBuilder,
)
from xyz_agent_context.schema.parsed_message import (
    ChatType,
    MessageContentType,
    ParsedMessage,
)


def _cred() -> NarramessengerCredential:
    return NarramessengerCredential(
        agent_id="agent_x",
        bearer_token="tok",
        backend_base_url="https://api.netmind.chat",
        matrix_homeserver_url="https://matrix.netmind.chat",
        matrix_user_id="@agent-x:matrix.netmind.chat",
        matrix_access_token="syt_x",
    )


def _message(*, raw: dict | None = None) -> ParsedMessage:
    return ParsedMessage(
        message_id="$evt:test",
        chat_id="!room:matrix.netmind.chat",
        sender_id="@u:test",
        sender_name="Alice",
        content="hi",
        content_type=MessageContentType.TEXT,
        chat_type=ChatType.PRIVATE,
        timestamp_ms=1_720_000_000_000,
        raw=raw or {},
    )


@pytest.mark.asyncio
async def test_get_conversation_history_always_returns_empty_for_bare_matrix_event():
    """The Direct-Matrix wrapped event carries no ``group_context`` or
    ``context`` field; the method MUST return ``[]``."""
    builder = NarramessengerContextBuilder(
        _message(), _cred(), agent_id="agent_x"
    )
    result = await builder.get_conversation_history(limit=20)
    assert result == []


@pytest.mark.asyncio
async def test_get_conversation_history_ignores_legacy_inline_history_keys():
    """Even if a caller managed to smuggle old-shape Gateway history
    into ``ParsedMessage.raw`` (e.g. from a resurrected polling code
    path), the method still returns ``[]`` — the contract is now
    "history comes from ChatModule memory, never from the invocation
    payload"."""
    legacy_raw = {
        "group_context": {
            "history_messages": [
                {
                    "sender": "bob",
                    "body": "legacy content",
                    "origin_server_ts": "1720000000000",
                }
            ]
        },
        "context": [
            {"role": "user", "sender": "bob", "content": "legacy content"}
        ],
    }
    builder = NarramessengerContextBuilder(
        _message(raw=legacy_raw), _cred(), agent_id="agent_x"
    )
    result = await builder.get_conversation_history(limit=20)
    assert result == []


@pytest.mark.asyncio
async def test_get_room_members_always_returns_empty_for_bare_matrix_event():
    """Direct-Matrix wrapped event has no ``group_context.members``
    field; the method MUST return ``[]``. Live roster access is via
    the ``narra_room_members`` MCP tool, not the prompt."""
    builder = NarramessengerContextBuilder(
        _message(), _cred(), agent_id="agent_x"
    )
    result = await builder.get_room_members()
    assert result == []


@pytest.mark.asyncio
async def test_get_room_members_ignores_legacy_inline_members_key():
    """Same as the history test: even a smuggled Gateway-shape
    ``group_context.members`` MUST NOT surface — the roster contract
    is now "on-demand via the MCP tool, never on the prompt"."""
    legacy_raw = {
        "group_context": {
            "members": [
                {
                    "matrix_user_id": "@bob:test",
                    "display_name": "Bob",
                }
            ]
        }
    }
    builder = NarramessengerContextBuilder(
        _message(raw=legacy_raw), _cred(), agent_id="agent_x"
    )
    result = await builder.get_room_members()
    assert result == []
