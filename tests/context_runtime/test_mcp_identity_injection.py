"""
@file_name: test_mcp_identity_injection.py
@author: NarraNexus
@date: 2026-08-01
@description: ContextRuntime must tell each module MCP server WHICH agent is
calling (P1 evt_0dcee899, injection half).

``module/_mcp_identity.py`` can only correct a tool's ``agent_id`` if the
identity actually reaches the server, and there is exactly one place that
decides: the per-agent ``mcp_servers`` spec built in
``build_input_for_framework``. Both CLI adapters consume that same dict, so
if the headers are missing here the whole fix silently degrades to the old
"trust the model" behaviour — with no error anywhere. Hence this test.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from narranexus.platform.module_system.base import XYZBaseModule
from narranexus.platform.module_system._mcp_identity import (
    AGENT_ID_HEADER,
    BEARER_AGENT_PREFIX,
)

AGENT = "agent_d8795abf5021"


class _FakeModule:


    # the base class composes the three tool hooks into the Assemble cell (batch 5c)


    contribute_tools = XYZBaseModule.contribute_tools
    """A module that only needs to answer mcp_server()."""

    def __init__(self, name: str, url: str):
        self._name, self._url = name, url

    async def mcp_server(self):
        return SimpleNamespace(server_name=self._name, server_url=self._url)

    async def contribute_turn_context(self, ctx_data):
        return ""

    async def expressive_tools(self, ctx_data=None):
        # Accepts ctx_data since #228 — context_runtime calls it WITH the
        # arg and logs "declaration DROPPED" (error) on signature drift, so a
        # stale fake here would silently exercise that error path.
        return []


def _instance(module_class: str, module) -> SimpleNamespace:
    return SimpleNamespace(
        module_class=module_class, instance_id=f"{module_class}_1", module=module
    )


@pytest.mark.asyncio
async def test_mcp_spec_carries_the_callers_identity():
    from narranexus.platform.context_runtime.context_runtime import ContextRuntime
    from narranexus.platform.schema import ContextData

    runtime = ContextRuntime(agent_id=AGENT, user_id="user_tc",
                             database_client=object())
    instances = [
        _instance("SocialNetworkModule", _FakeModule("social_network_module",
                                                    "http://127.0.0.1:7802/sse")),
        _instance("MessageBusModule", _FakeModule("message_bus_module",
                                                 "http://127.0.0.1:7810/sse")),
    ]

    # returns (messages, mcp_servers, disallowed_tools, expressive_tools)
    _msgs, mcp_servers, *_rest = await runtime.build_input_for_framework(
        messages=[],
        system_prompt="sys",
        active_instances=instances,
        ctx_data=ContextData(agent_id=AGENT, input_content="hi"),
    )

    assert set(mcp_servers) == {"social_network_module", "message_bus_module"}
    for name, spec in mcp_servers.items():
        headers = spec.get("headers") or {}
        # The explicit header (claude adapter forwards arbitrary headers)...
        assert headers.get(AGENT_ID_HEADER) == AGENT, f"{name} lost the identity header"
        # ...and the borrowed bearer (the only shape codex can transmit).
        # Fields 2-4 (turn source / errand scope) are unknown on this turn and
        # stay empty; the turn owner rides field 5.
        assert headers.get("Authorization") == f"Bearer {BEARER_AGENT_PREFIX}{AGENT}~~~~user_tc"
        # The URL must be untouched — identity rides headers, not the query
        # string (a query param is LOST on the SSE transport).
        assert "?" not in spec["url"]


@pytest.mark.asyncio
async def test_injected_identity_is_this_agent_not_a_constant():
    """Guard against a hardcoded/stale id: two runtimes must inject their own."""
    from narranexus.platform.context_runtime.context_runtime import ContextRuntime
    from narranexus.platform.schema import ContextData

    seen = {}
    for agent in (AGENT, "agent_25dec880a426"):
        runtime = ContextRuntime(agent_id=agent, user_id="user_tc",
                                 database_client=object())
        _m, servers, *_r = await runtime.build_input_for_framework(
            messages=[],
            system_prompt="sys",
            active_instances=[
                _instance("SocialNetworkModule",
                          _FakeModule("social_network_module", "http://x/sse"))
            ],
            ctx_data=ContextData(agent_id=AGENT, input_content="hi"),
        )
        seen[agent] = servers["social_network_module"]["headers"][AGENT_ID_HEADER]

    assert seen == {AGENT: AGENT, "agent_25dec880a426": "agent_25dec880a426"}


async def _bus_turn_headers(extra_data: dict) -> dict:
    from narranexus.platform.context_runtime.context_runtime import ContextRuntime
    from narranexus.platform.schema import ContextData, WorkingSource

    runtime = ContextRuntime(agent_id=AGENT, user_id="user_tc",
                             database_client=object())
    ctx = ContextData(agent_id=AGENT, input_content="hi")
    ctx.working_source = WorkingSource.MESSAGE_BUS
    ctx.extra_data = extra_data
    _m, servers, *_r = await runtime.build_input_for_framework(
        messages=[],
        system_prompt="sys",
        active_instances=[
            _instance("MessageBusModule",
                      _FakeModule("message_bus_module", "http://x/sse"))
        ],
        ctx_data=ctx,
    )
    return servers["message_bus_module"]["headers"]


@pytest.mark.asyncio
async def test_errand_continuation_bus_turn_injects_its_errand_scope():
    """A MESSAGE_BUS turn that continues the agent's OWN errand must tell the
    tools WHICH peer/channel that errand is with, so a send aimed at it can
    stamp itself as a question (P1 path A). The turn source itself stays
    "message_bus": stamping the whole turn also mislabels the answers this
    same turn gives to unrelated peers, which reproduced the P1 one seat over
    (2026-08-03 review).
    """
    from narranexus.platform.module_system._mcp_identity import (
        BEARER_FIELD_SEP,
        ERRAND_CHANNEL_HEADER,
        ERRAND_PEER_HEADER,
        TURN_SOURCE_HEADER,
    )

    headers = await _bus_turn_headers({
        "bus_errand_peer": "agent_yushu",
        "bus_errand_channel": "ch_dm_errand",
    })

    assert headers[TURN_SOURCE_HEADER] == "message_bus"
    assert headers[ERRAND_PEER_HEADER] == "agent_yushu"
    assert headers[ERRAND_CHANNEL_HEADER] == "ch_dm_errand"
    # Codex forwards nothing but the bearer, so the scope must ride it too.
    assert headers["Authorization"] == (
        f"Bearer {BEARER_AGENT_PREFIX}{AGENT}"
        f"{BEARER_FIELD_SEP}message_bus"
        f"{BEARER_FIELD_SEP}agent_yushu"
        f"{BEARER_FIELD_SEP}ch_dm_errand"
        f"{BEARER_FIELD_SEP}user_tc"
    )


@pytest.mark.asyncio
async def test_a_plain_bus_turn_carries_no_errand_scope():
    """Answering a peer is not an errand of ours — nothing to inherit."""
    from narranexus.platform.module_system._mcp_identity import (
        BEARER_FIELD_SEP,
        ERRAND_CHANNEL_HEADER,
        ERRAND_PEER_HEADER,
    )

    headers = await _bus_turn_headers({"bus_channel_id": "ch_dm_1"})

    assert ERRAND_PEER_HEADER not in headers
    assert ERRAND_CHANNEL_HEADER not in headers
    # Empty errand fields stay as positional blanks so the owner keeps slot 5.
    assert headers["Authorization"] == (
        f"Bearer {BEARER_AGENT_PREFIX}{AGENT}{BEARER_FIELD_SEP}message_bus"
        f"{BEARER_FIELD_SEP}{BEARER_FIELD_SEP}{BEARER_FIELD_SEP}user_tc"
    )


@pytest.mark.asyncio
async def test_mcp_spec_carries_the_turn_owner():
    """user_id rides the same seam — and an ownerless turn omits it."""
    from narranexus.platform.context_runtime.context_runtime import ContextRuntime
    from narranexus.platform.module_system._mcp_identity import BEARER_FIELD_SEP, USER_ID_HEADER
    from narranexus.platform.schema import ContextData

    for user_id, expect in (("user_tc", "user_tc"), (None, None)):
        runtime = ContextRuntime(agent_id=AGENT, user_id=user_id,
                                 database_client=object())
        _m, servers, *_r = await runtime.build_input_for_framework(
            messages=[],
            system_prompt="sys",
            active_instances=[
                _instance("SocialNetworkModule",
                          _FakeModule("social_network_module", "http://x/sse"))
            ],
            ctx_data=ContextData(agent_id=AGENT, input_content="hi"),
        )
        headers = servers["social_network_module"]["headers"]
        assert headers.get(USER_ID_HEADER) == expect
        if expect is None:
            # No trailing blank either: the bearer drops empty tail fields.
            assert not headers["Authorization"].endswith(BEARER_FIELD_SEP)


async def _headers_with_event_id(extra_data: dict, event_id: str) -> dict:
    from narranexus.platform.context_runtime.context_runtime import ContextRuntime
    from narranexus.platform.schema import ContextData, WorkingSource

    runtime = ContextRuntime(agent_id=AGENT, user_id="user_tc",
                             database_client=object(), event_id=event_id)
    ctx = ContextData(agent_id=AGENT, input_content="hi")
    ctx.working_source = WorkingSource.MESSAGE_BUS
    ctx.extra_data = extra_data
    _m, servers, *_r = await runtime.build_input_for_framework(
        messages=[],
        system_prompt="sys",
        active_instances=[
            _instance("MessageBusModule",
                      _FakeModule("message_bus_module", "http://x/sse"))
        ],
        ctx_data=ctx,
    )
    return servers["message_bus_module"]["headers"]


@pytest.mark.asyncio
async def test_a_root_turn_stamps_its_own_event_id_as_root_run_id():
    """A turn whose trigger carries no `root_run_id` (nothing sent this turn's
    prompt on behalf of an existing tree) IS the root of a new tree — exactly
    what `RunRecorder` already writes into `events.root_run_id` for it
    (`inherited_root_run_id or run_id`).

    Before this fix the MCP header stayed "" on a root turn, so any work-board
    item this turn created (e.g. via `record_handoffs`) was stamped with an
    EMPTY `root_run_id`. Stopping the run later reads the correct, non-empty
    `root_run_id` off the `events` row and calls `pause_by_root(root)`, but
    that lookup is `WHERE root_run_id = root` — it can never match a row that
    was written with "" instead, so the item is silently orphaned forever
    (in_progress) even though the run itself DID stop (GitHub #124).
    """
    from narranexus.platform.module_system._mcp_identity import ROOT_RUN_ID_HEADER

    headers = await _headers_with_event_id({"bus_channel_id": "ch_dm_1"}, event_id="evt_root_1")

    assert headers.get(ROOT_RUN_ID_HEADER) == "evt_root_1"


@pytest.mark.asyncio
async def test_a_child_turn_still_inherits_its_parents_root_run_id():
    """A turn whose trigger DOES carry a `root_run_id` (this turn's prompt was
    itself sent by a run inside an existing tree) must keep tracing that
    tree — never overwrite it with its own event id."""
    from narranexus.platform.module_system._mcp_identity import ROOT_RUN_ID_HEADER

    headers = await _headers_with_event_id(
        {"bus_channel_id": "ch_dm_1", "root_run_id": "evt_root_1"},
        event_id="evt_child_2",
    )

    assert headers.get(ROOT_RUN_ID_HEADER) == "evt_root_1"
