"""
@file_name: test_mcp_server_ordering.py
@author: NarraNexus
@date: 2026-07-25
@description: R4c tool-order determinism — experiment E2 (2026-07-25) proved
the request's tools array reshuffles across runs (identical sets, different
order), capping the cacheable prefix at the first transposed tool.
ContextRuntime's mcp_servers dict is the order source WE control: it must be
sorted by server name, independent of active_instances iteration order.
(The residual cross-server merge order inside the CLI is CLI-internal.)
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from xyz_agent_context.context_runtime.context_runtime import ContextRuntime
from xyz_agent_context.schema import ContextData
from xyz_agent_context.settings import settings

AGENT_ID = "agent_mcp_order"


class _FakeModule:
    def __init__(self, name: str, priority: int, server_name: str, url: str):
        self.config = SimpleNamespace(name=name, priority=priority)
        self._mcp = SimpleNamespace(server_name=server_name, server_url=url)

    async def get_mcp_config(self):
        return self._mcp

    async def get_disallowed_tools(self):
        return []

    async def get_turn_context(self, ctx_data) -> str:
        return ""


def _inst(module: _FakeModule) -> SimpleNamespace:
    return SimpleNamespace(module_class=module.config.name, module=module, instance_id="i")


def _instances_shuffled() -> list[SimpleNamespace]:
    # Deliberately non-alphabetical instance order (mimics active_instances
    # arriving in narrative/module-load order).
    return [
        _inst(_FakeModule("SocialNetworkModule", 3, "social_network", "http://localhost:7802/sse")),
        _inst(_FakeModule("AwarenessModule", 0, "awareness", "http://localhost:7801/sse")),
        _inst(_FakeModule("ChatModule", 1, "chat_module", "http://localhost:7804/sse")),
        _inst(_FakeModule("JobModule", 4, "job", "http://localhost:7803/sse")),
    ]


async def _collect_server_order(monkeypatch, instances) -> list[str]:
    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", True)
    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.agent_id = AGENT_ID
    ctx = ContextData(agent_id=AGENT_ID, user_id=None, input_content="hi")
    _messages, mcp_servers, _dis, _expr = await runtime.build_input_for_framework(
        messages=[],
        system_prompt="SYSTEM",
        active_instances=instances,
        ctx_data=ctx,
        narrative_list=None,
    )
    return list(mcp_servers.keys())


@pytest.mark.asyncio
async def test_mcp_servers_sorted_by_name(monkeypatch):
    order = await _collect_server_order(monkeypatch, _instances_shuffled())
    assert order == ["awareness", "chat_module", "job", "social_network"]


@pytest.mark.asyncio
async def test_mcp_server_order_independent_of_instance_order(monkeypatch):
    a = await _collect_server_order(monkeypatch, _instances_shuffled())
    b = await _collect_server_order(monkeypatch, list(reversed(_instances_shuffled())))
    assert a == b
