"""
@file_name: test_expressive_collection.py
@date: 2026-07-31
@description: Expressive/delivery tool collection — the platform declares
the turn's reply surface to the framework (NexusPower reply contract).

``build_input_for_framework`` collects every active module's
``get_expressive_tools()`` and orders the result by the TOTAL
(priority, module_class) order (R4d) — NOT by active_instances order,
which is created_at-driven and would let a later-created channel
instance steal the first slot. The first entry is the turn's default
reply tool and lands in the framework's STABLE prompt prefix, so this
order must be priority-driven and deterministic. Crashing modules
contribute nothing — fail-open, same posture as ``get_disallowed_tools``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from xyz_agent_context.context_runtime.context_runtime import ContextRuntime
from xyz_agent_context.schema import ContextData
from xyz_agent_context.settings import settings

AGENT_ID = "agent_expressive"


class _FakeModule:
    def __init__(
        self,
        name: str,
        priority: int,
        expressive: list[str] | None = None,
        crash: bool = False,
    ):
        self.config = SimpleNamespace(name=name, priority=priority)
        self._expressive = expressive
        self._crash = crash

    async def get_mcp_config(self):
        return None

    async def get_disallowed_tools(self):
        return []

    async def get_expressive_tools(self):
        if self._crash:
            raise RuntimeError("boom")
        return list(self._expressive or [])

    async def get_turn_context(self, ctx_data) -> str:
        return ""


def _inst(module) -> SimpleNamespace:
    return SimpleNamespace(module_class=module.config.name, module=module, instance_id="i")


async def _collect(instances, monkeypatch) -> list[str]:
    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", True)
    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.agent_id = AGENT_ID
    ctx = ContextData(agent_id=AGENT_ID, user_id=None, input_content="hi")
    _messages, _mcp, _dis, expressive = await runtime.build_input_for_framework(
        messages=[],
        system_prompt="SYSTEM",
        active_instances=instances,
        ctx_data=ctx,
        narrative_list=None,
    )
    return expressive


CHAT_TOOL = "mcp__chat_module__send_message_to_user_directly"
LARK_TOOL = "mcp__lark_module__lark_cli"


@pytest.mark.asyncio
async def test_priority_order_wins_over_instance_order(monkeypatch):
    """active_instances arrives in created_at order (a later-created Lark
    instance sits BEFORE Chat) — the collected list must still put chat
    (priority 1) first, because the first entry becomes the constitution's
    default reply tool, frozen into the stable prompt prefix."""
    instances = [
        _inst(_FakeModule("LarkModule", 6, [LARK_TOOL])),
        _inst(_FakeModule("ChatModule", 1, [CHAT_TOOL])),
    ]
    assert await _collect(instances, monkeypatch) == [CHAT_TOOL, LARK_TOOL]


@pytest.mark.asyncio
async def test_dedupe_and_fail_open(monkeypatch):
    instances = [
        _inst(_FakeModule("ChatModule", 1, [CHAT_TOOL])),
        _inst(_FakeModule("Broken", 3, crash=True)),
        _inst(_FakeModule("LarkModule", 6, [LARK_TOOL, CHAT_TOOL])),  # dupe chat
    ]
    assert await _collect(instances, monkeypatch) == [CHAT_TOOL, LARK_TOOL]


@pytest.mark.asyncio
async def test_equal_priority_breaks_ties_by_module_class(monkeypatch):
    """Same total order as R4d everywhere: (priority, module_class)."""
    instances = [
        _inst(_FakeModule("ZChannel", 6, ["mcp__z__send"])),
        _inst(_FakeModule("AChannel", 6, ["mcp__a__send"])),
    ]
    assert await _collect(instances, monkeypatch) == ["mcp__a__send", "mcp__z__send"]
