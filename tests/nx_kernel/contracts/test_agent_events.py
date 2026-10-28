"""
@file_name: test_agent_events.py
@author: Bin Liang
@date: 2026-09-03
@description: The agent-loop event contract lives in contracts (the legacy loop module is gone); wire values are pinned.
"""
from __future__ import annotations

from narranexus.contracts import API_VERSIONS
from narranexus.contracts import agent_events as contract
from tests.snapshots._approval import approve


def test_every_public_name_exists_and_the_legacy_module_is_gone():
    import importlib.util

    # Concrete membership, not "every name in __all__ resolves" (which is
    # trivially true of __all__ itself — dropping a name silently shrinks
    # both sides together). Dropping or renaming an event name here is
    # exactly the regression this test exists to catch.
    assert set(contract.__all__) == {
        "CLI_ERROR_TYPES", "DATA_TYPE_DONE", "DATA_TYPE_DONE_SUPERSEDED_KEY",
        "DATA_TYPE_ERROR", "DATA_TYPE_REPLY_DELTA", "DATA_TYPE_RETRY",
        "DATA_TYPE_TEXT_DELTA", "DATA_TYPE_USAGE", "ITEM_TYPE_MESSAGE_OUTPUT",
        "ITEM_TYPE_PLAN", "ITEM_TYPE_THINKING", "ITEM_TYPE_TOOL_CALL",
        "ITEM_TYPE_TOOL_CALL_OUTPUT", "RawResponseData", "RawResponseEvent",
        "RunItem", "RunItemStreamEvent", "TYPE_RAW_RESPONSE_EVENT",
        "TYPE_RUN_ITEM_STREAM_EVENT", "USAGE_CACHE_CREATION_KEY",
        "USAGE_CACHE_READ_KEYS", "cli_error_self_serviceable",
        "raw_error_event", "raw_text_delta_event",
    }
    for name in contract.__all__:
        assert hasattr(contract, name), name
    assert importlib.util.find_spec("narranexus.platform.agent_framework.loop.events") is None


def test_wire_values_are_pinned():
    approve(
        "agent_events",
        {
            "families": [contract.TYPE_RAW_RESPONSE_EVENT, contract.TYPE_RUN_ITEM_STREAM_EVENT],
            "data_types": [
                contract.DATA_TYPE_TEXT_DELTA, contract.DATA_TYPE_DONE, contract.DATA_TYPE_ERROR,
                contract.DATA_TYPE_USAGE, contract.DATA_TYPE_REPLY_DELTA, contract.DATA_TYPE_RETRY,
            ],
            "done_superseded_key": contract.DATA_TYPE_DONE_SUPERSEDED_KEY,
            "item_types": [
                contract.ITEM_TYPE_THINKING, contract.ITEM_TYPE_TOOL_CALL,
                contract.ITEM_TYPE_TOOL_CALL_OUTPUT, contract.ITEM_TYPE_MESSAGE_OUTPUT, contract.ITEM_TYPE_PLAN,
            ],
            "cli_error_types": sorted(contract.CLI_ERROR_TYPES),
            "usage_cache_read_keys": list(contract.USAGE_CACHE_READ_KEYS),
            "usage_cache_creation_key": contract.USAGE_CACHE_CREATION_KEY,
            "api_version": API_VERSIONS["agent_events"],
        },
    )


def test_constructors_build_contract_shapes():
    ev = contract.raw_text_delta_event("hi")
    assert ev == {"type": "raw_response_event", "data": {"type": "response.text.delta", "delta": "hi"}}
    err = contract.raw_error_event("boom", "server_error")
    assert err["data"]["error_type"] in contract.CLI_ERROR_TYPES
