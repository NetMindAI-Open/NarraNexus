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
        owns_source: str | None = None,
    ):
        self.config = SimpleNamespace(name=name, priority=priority)
        self._expressive = expressive
        self._crash = crash
        if owns_source is not None:
            self._owns_source = owns_source
            self.owns_working_source = self._owns_working_source

    def _owns_working_source(self, working_source) -> bool:
        return working_source == self._owns_source

    async def get_mcp_config(self):
        return None

    async def get_disallowed_tools(self):
        return []

    async def get_expressive_tools(self, ctx_data=None):
        if self._crash:
            raise RuntimeError("boom")
        return list(self._expressive or [])

    async def get_turn_context(self, ctx_data) -> str:
        return ""


def _inst(module) -> SimpleNamespace:
    return SimpleNamespace(module_class=module.config.name, module=module, instance_id="i")


async def _collect(instances, monkeypatch, working_source=None, extra=None) -> list[str]:
    monkeypatch.setattr(settings, "prompt_turn_context_relocation_enabled", True)
    runtime = ContextRuntime.__new__(ContextRuntime)
    runtime.agent_id = AGENT_ID
    runtime.user_id = None  # __init__ skipped; identity seam reads it
    ctx = ContextData(agent_id=AGENT_ID, user_id=None, input_content="hi")
    if working_source is not None:
        ctx.working_source = working_source
    if extra:
        ctx.extra_data.update(extra)
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


BUS_TOOL = "mcp__message_bus_module__bus_send_message"


@pytest.mark.asyncio
async def test_origin_module_declaration_sorts_first(monkeypatch):
    """The module that OWNS the turn's working_source outranks priority:
    its first tool becomes the default reply tool, so a bus-triggered
    turn defaults to the bus delivery tool — not the owner-chat tool
    that priority order alone would put first (the model would be told
    "this turn's default: send_message_to_user_directly" on a turn
    whose contact came over the bus)."""
    instances = [
        _inst(_FakeModule("ChatModule", 1, [CHAT_TOOL])),
        _inst(_FakeModule("MessageBusModule", 5, [BUS_TOOL], owns_source="message_bus")),
    ]
    collected = await _collect(instances, monkeypatch, working_source="message_bus")
    assert collected == [BUS_TOOL, CHAT_TOOL]


@pytest.mark.asyncio
async def test_origin_first_only_applies_when_source_matches(monkeypatch):
    """On a chat turn the bus module does not own the source — plain
    (priority, module_class) order stands."""
    instances = [
        _inst(_FakeModule("ChatModule", 1, [CHAT_TOOL])),
        _inst(_FakeModule("MessageBusModule", 5, [BUS_TOOL], owns_source="message_bus")),
    ]
    collected = await _collect(instances, monkeypatch, working_source="chat")
    assert collected == [CHAT_TOOL, BUS_TOOL]


@pytest.mark.asyncio
async def test_modules_without_owns_hook_keep_priority_order(monkeypatch):
    """Fail-open: a module that never heard of owns_working_source (or a
    turn with no working_source) sorts by (priority, module_class) as
    before."""
    instances = [
        _inst(_FakeModule("LarkModule", 6, [LARK_TOOL])),
        _inst(_FakeModule("ChatModule", 1, [CHAT_TOOL])),
    ]
    assert await _collect(instances, monkeypatch, working_source="lark") == [
        CHAT_TOOL,
        LARK_TOOL,
    ]


@pytest.mark.asyncio
async def test_real_modules_bus_turn_defaults_to_bus_delivery(monkeypatch):
    """Integration seam, real modules: on a MESSAGE_BUS turn the collected
    surface leads with the bus delivery tools (origin-first), with the
    owner-chat tool still present for Owner Relay. This is the exact list
    both frameworks render — NexusPower's per-step reminder and the claude
    adapter's user-message reminder."""
    from unittest.mock import MagicMock

    from xyz_agent_context.module.chat_module.chat_module import ChatModule
    from xyz_agent_context.module.message_bus_module.message_bus_module import (
        MessageBusModule,
    )

    bus = MessageBusModule(agent_id=AGENT_ID, user_id=None, database_client=MagicMock())
    chat = ChatModule(agent_id=AGENT_ID, user_id=None, database_client=MagicMock())
    instances = [
        SimpleNamespace(module_class="ChatModule", module=chat, instance_id="i1"),
        SimpleNamespace(module_class="MessageBusModule", module=bus, instance_id="i2"),
    ]

    collected = await _collect(instances, monkeypatch, working_source="message_bus")
    assert collected[0] == "mcp__message_bus_module__bus_send_message"
    assert collected[1] == "mcp__message_bus_module__bus_send_to_agent"
    assert "mcp__chat_module__send_message_to_user_directly" in collected


@pytest.mark.asyncio
async def test_team_room_turn_has_empty_reply_surface(monkeypatch):
    """Team rooms deliver via plain-text auto-post and their prompt FORBIDS
    delivery tools — the turn's reply surface must be EMPTY, from every
    declarer. Gating only the bus module left ChatModule's unconditional
    declaration in the list, which made both frameworks' reminders assert
    "plain text is never delivered" right next to the team prompt saying
    the opposite (PR #230 review, Critical #1). Central gate: the
    collection returns [] whenever the bus_team_room marker is set."""
    instances = [
        _inst(_FakeModule("ChatModule", 1, [CHAT_TOOL])),
        _inst(_FakeModule("LarkModule", 6, [LARK_TOOL])),
        _inst(_FakeModule("MessageBusModule", 5, [BUS_TOOL], owns_source="message_bus")),
    ]
    collected = await _collect(
        instances, monkeypatch,
        working_source="message_bus", extra={"bus_team_room": True},
    )
    assert collected == []


def test_every_module_expressive_signature_accepts_ctx_data():
    """Guard against exactly the failure that muted ChatModule once: the
    base signature grew a positional ctx_data and an override that keeps
    the old (self)-only shape raises TypeError at the collection site,
    where fail-open silently drops that module's whole declaration."""
    import inspect

    from xyz_agent_context.module import MODULE_MAP

    for name, cls in MODULE_MAP.items():
        fn = cls.get_expressive_tools
        params = list(inspect.signature(fn).parameters.values())
        assert any(
            p.name == "ctx_data" or p.kind is inspect.Parameter.VAR_POSITIONAL
            for p in params
        ), (
            f"{name}.get_expressive_tools must accept ctx_data - a stale "
            f"(self)-only override is silently dropped by the fail-open "
            f"collection site"
        )
