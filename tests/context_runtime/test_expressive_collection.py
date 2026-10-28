"""
@file_name: test_expressive_collection.py
@date: 2026-07-31
@description: Expressive/delivery tool collection — the platform declares
the turn's reply surface to the framework (NexusPower reply contract).

``build_input_for_framework`` collects every active module's
``expressive_tools()`` and orders the result by the TOTAL
(priority, module_class) order (R4d) — NOT by active_instances order,
which is created_at-driven and would let a later-created channel
instance steal the first slot. The first entry is the turn's default
reply tool and lands in the framework's STABLE prompt prefix, so this
order must be priority-driven and deterministic. Crashing modules
contribute nothing — fail-open, same posture as ``disallowed_tools``.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from narranexus.platform.module_system.base import XYZBaseModule
from narranexus.platform.context_runtime.context_runtime import ContextRuntime
from narranexus.platform.schema import ContextData
from narranexus.platform.settings import settings

AGENT_ID = "agent_expressive"


class _FakeModule:


    # the base class composes the three tool hooks into the Assemble cell (batch 5c)


    contribute_tools = XYZBaseModule.contribute_tools
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
            self.claims_source = self._owns_working_source

    def _owns_working_source(self, working_source) -> bool:
        return working_source == self._owns_source

    async def mcp_server(self):
        return None

    async def disallowed_tools(self, ctx_data=None):
        return []

    async def expressive_tools(self, ctx_data=None):
        if self._crash:
            raise RuntimeError("boom")
        return list(self._expressive or [])

    async def contribute_turn_context(self, ctx_data) -> str:
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
    _messages, _mcp, disallowed, expressive, _deferred = await runtime.build_input_for_framework(
        messages=[],
        system_prompt="SYSTEM",
        active_instances=instances,
        ctx_data=ctx,
        narrative_list=None,
    )
    _collect.last_disallowed = disallowed  # the desk's other half, for _desk()
    return expressive


async def _desk(instances, monkeypatch, **kw) -> tuple[list[str], list[str]]:
    """(declared, suppressed) from one real build — the two halves of the desk."""
    declared = await _collect(instances, monkeypatch, **kw)
    return declared, list(_collect.last_disallowed or [])


CHAT_TOOL = "mcp__chat_module__reply_owner"
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


BUS_TOOL = "mcp__message_bus_module__message_team"


@pytest.mark.asyncio
async def test_origin_module_declaration_sorts_first(monkeypatch):
    """The module that OWNS the turn's working_source outranks priority:
    its first tool becomes the default reply tool, so a bus-triggered
    turn defaults to the bus delivery tool — not the owner-chat tool
    that priority order alone would put first (the model would be told
    "this turn's default: reply_owner" on a turn
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
    """Fail-open: a module that never heard of claims_source (or a
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
    surface LEADS with the peer send (origin-first), with the owner-notify tool
    still present for Owner Relay. This is the exact list both frameworks
    render — NexusPower's per-step reminder and the claude adapter's
    user-message reminder.

    Two names, not three: since 2026-08-17 each turn declares exactly ONE send
    verb (`message_agent` on a peer turn, `message_team` in a room) plus the
    owner lane. The old list carried both bus sends at once and left the model
    to pick."""
    from unittest.mock import MagicMock

    from narranexus_plugins.chat_module.chat_module import ChatModule
    from narranexus_plugins.message_bus_module.message_bus_module import (
        MessageBusModule,
    )

    bus = MessageBusModule(agent_id=AGENT_ID, user_id=None, database_client=MagicMock())
    chat = ChatModule(agent_id=AGENT_ID, user_id=None, database_client=MagicMock())
    instances = [
        SimpleNamespace(module_class="ChatModule", module=chat, instance_id="i1"),
        SimpleNamespace(module_class="MessageBusModule", module=bus, instance_id="i2"),
    ]

    collected = await _collect(instances, monkeypatch, working_source="message_bus")
    assert collected[0] == "mcp__message_bus_module__message_agent"
    assert collected[1] == "mcp__chat_module__notify_owner"
    assert len(collected) == 2


@pytest.mark.asyncio
async def test_team_room_turn_declares_the_room_send(monkeypatch):
    """A team room is no longer the one surface with an empty reply surface.

    It used to be: the agent's plain text auto-posted, the prompt forbade
    delivery tools, and the declaration was emptied so neither framework's
    reminder would contradict that. The cost was an exception to the most
    fundamental rule in the system ("plain text reaches nobody"), asserted by
    three layers of which only one could be switched off — six review rounds of
    contradictions grew out of it.

    Since 2026-08-17 the room takes a tool call like everywhere else, so the
    surface names `message_team` and the general rule is true again.
    """
    from unittest.mock import MagicMock

    from narranexus_plugins.chat_module.chat_module import ChatModule
    from narranexus_plugins.message_bus_module.message_bus_module import (
        MessageBusModule,
    )
    from narranexus.platform.schema import BUS_TEAM_ROOM_EXTRA_KEY

    bus = MessageBusModule(agent_id=AGENT_ID, user_id=None, database_client=MagicMock())
    chat = ChatModule(agent_id=AGENT_ID, user_id=None, database_client=MagicMock())
    instances = [
        SimpleNamespace(module_class="ChatModule", module=chat, instance_id="i1"),
        SimpleNamespace(module_class="MessageBusModule", module=bus, instance_id="i2"),
    ]

    collected = await _collect(
        instances, monkeypatch, working_source="message_bus",
        extra={BUS_TEAM_ROOM_EXTRA_KEY: True},
    )
    assert collected[0] == "mcp__message_bus_module__message_team"
    assert "mcp__chat_module__notify_owner" in collected
    assert "mcp__message_bus_module__message_agent" not in collected



def test_every_module_expressive_signature_accepts_ctx_data():
    """Guard against exactly the failure that muted ChatModule once: the
    base signature grew a positional ctx_data and an override that keeps
    the old (self)-only shape raises TypeError at the collection site,
    where fail-open silently drops that module's whole declaration."""
    import inspect

    from narranexus.platform.module_system import module_registry

    for name, cls in module_registry.items():
        fn = cls.expressive_tools
        params = list(inspect.signature(fn).parameters.values())
        assert any(
            p.name == "ctx_data" or p.kind is inspect.Parameter.VAR_POSITIONAL
            for p in params
        ), (
            f"{name}.expressive_tools must accept ctx_data - a stale "
            f"(self)-only override is silently dropped by the fail-open "
            f"collection site"
        )


@pytest.mark.asyncio
async def test_the_desk_never_declares_a_tool_it_suppresses(monkeypatch):
    """The two halves of the desk, asserted TOGETHER through the real build.

    Every other guard on this seam calls the two module hooks directly, and that
    is how the worst version of this bug shipped: suppression is asked FIRST at
    the call site, so a module that read the turn from state the declaration had
    left behind answered every team turn on an empty instance — declaring
    `message_team` while removing its schema. Both hooks were individually
    correct; the desk was incoherent, and no test looked at the desk.

    `disallowed_tools` strips the schema on both frameworks, so an overlap means
    the reply reminder names a tool the model cannot see: nothing reaches the
    room, every turn, silently.

    NB (2026-08-20): the historical bug above was a MessageBusModule team turn.
    That module no longer drops a send verb by trigger channel — on an ordinary
    bus turn it suppresses nothing, so the overlap it could produce is trivially
    empty. The guard still bites where suppression is real: patrol (both verbs
    off) and ChatModule (the inapplicable owner register off). It is the
    coherence invariant that is asserted, not the old message_bus drop.
    """
    from unittest.mock import MagicMock

    from narranexus_plugins.chat_module.chat_module import ChatModule
    from narranexus_plugins.message_bus_module.message_bus_module import (
        MessageBusModule,
    )
    from narranexus.platform.schema import BUS_TEAM_ROOM_EXTRA_KEY

    for label, source, extra in (
        ("team room", "message_bus", {BUS_TEAM_ROOM_EXTRA_KEY: True}),
        ("peer DM", "message_bus", None),
        ("owner chat", "chat", None),
    ):
        # Fresh instances per turn kind: a module that carried the previous
        # turn's answer would pass a loop that reused them.
        bus = MessageBusModule(
            agent_id=AGENT_ID, user_id=None, database_client=MagicMock()
        )
        chat = ChatModule(
            agent_id=AGENT_ID, user_id=None, database_client=MagicMock()
        )
        instances = [
            SimpleNamespace(module_class="ChatModule", module=chat, instance_id="i1"),
            SimpleNamespace(
                module_class="MessageBusModule", module=bus, instance_id="i2"
            ),
        ]
        declared, suppressed = await _desk(
            instances, monkeypatch, working_source=source, extra=extra
        )
        assert declared, f"{label}: the turn declared no reply tool at all"
        overlap = set(declared) & set(suppressed)
        assert not overlap, (
            f"{label}: the reply reminder names {sorted(overlap)}, whose schema "
            f"the same turn removed — the agent is told to call a tool it has "
            f"no way to call"
        )


def test_every_module_disallow_signature_accepts_ctx_data():
    """The twin of the expressive-signature guard above, for the same reason.

    `disallowed_tools` grew a positional `ctx_data` on 2026-08-18. An
    override still on the old `(self)` shape raises TypeError at the call site,
    which fails OPEN — the module suppresses nothing, so both send verbs sit on
    the desk and the turn's rule is back to arguing with a visible tool.
    """
    import inspect

    from narranexus.platform.module_system import module_registry

    for name, cls in module_registry.items():
        hook = getattr(cls, "disallowed_tools", None)
        if hook is None:
            continue
        params = list(inspect.signature(hook).parameters)
        assert params[:2] == ["self", "ctx_data"], (
            f"{name}.disallowed_tools{tuple(params)} does not take ctx_data "
            f"— it will TypeError at the collection site and suppress nothing"
        )


@pytest.mark.asyncio
async def test_patrol_declares_nothing_and_keeps_both_verbs_off_the_desk(monkeypatch):
    """Patrol's reply IS its plain text, so its desk holds no send verb.

    A patrol turn carries the team-room marker too — it happens in a room — and
    on that marker alone `message_team` is declared as the turn's default reply
    tool and named by both frameworks' reply reminders. The patrol prompt, three
    lines up, says "write it as plain text (do NOT call message_team or
    message_agent — neither of those two calls is available on this turn)". On
    NexusPower it got worse than a contradiction: the mute-turn nudge fires when
    a turn closes with no reply-tool call, which for patrol is the SPECIFIED
    outcome, and the nudge's text tells the lead to call the forbidden tool. If
    obeyed, the status line posts as the lead chatting in the room, counted as a
    cascade hop.

    So the marker turns the declaration off rather than the prompt asking nicely
    — and the schemas come off the desk with it, because a prose prohibition
    beside a visible tool is the argument this redesign exists to stop having.
    """
    from unittest.mock import MagicMock

    from narranexus_plugins.message_bus_module.message_bus_module import (
        MessageBusModule,
    )
    from narranexus.platform.schema import (
        BUS_PLAIN_TEXT_TURN_EXTRA_KEY,
        BUS_TEAM_ROOM_EXTRA_KEY,
    )

    bus = MessageBusModule(agent_id=AGENT_ID, user_id=None, database_client=MagicMock())
    instances = [
        SimpleNamespace(module_class="MessageBusModule", module=bus, instance_id="i1")
    ]
    declared, suppressed = await _desk(
        instances, monkeypatch, working_source="message_bus",
        extra={BUS_TEAM_ROOM_EXTRA_KEY: True, BUS_PLAIN_TEXT_TURN_EXTRA_KEY: True},
    )

    assert declared == [], f"patrol declared a reply tool: {declared}"
    assert set(suppressed) == {
        "mcp__message_bus_module__message_agent",
        "mcp__message_bus_module__message_team",
    }, suppressed


def test_patrol_does_not_arm_the_mute_turn_nudge():
    """The other half, and the half that cannot be read off the desk.

    `expression_nudge` is a turn-profile field, not a declaration, so the desk
    test above stays green if patrol keeps arming it — and an armed nudge on a
    turn with an empty declaration is not harmless either: it is gated on
    `a.expression.names()` today, and the plausible future change is naming a
    generic fallback when the list is empty.
    """
    import inspect

    from narranexus.platform.message_bus.message_bus_trigger import MessageBusTrigger

    src = inspect.getsource(MessageBusTrigger._invoke_runtime)
    assert "if team_room and not patrol else None" in src, (
        "patrol arms the mute-turn nudge again — on a surface whose correct "
        "outcome is closing with plain text or in silence"
    )

    body = inspect.getsource(MessageBusTrigger._patrol_body)
    assert "patrol=True" in body, (
        "the patrol lane stopped declaring itself as one, so its turn looks "
        "like an ordinary team-room turn to every hook downstream"
    )


@pytest.mark.asyncio
async def test_a_stale_disallow_signature_is_logged_loudly(monkeypatch, caplog):
    """Fail-open is right; failing open QUIETLY on a signature drift is not.

    `expressive_tools` grew this arm after a stale override silently muted
    ChatModule's whole declaration. `disallowed_tools` grew its `ctx_data`
    parameter on 2026-08-18, which puts it in exactly that position — and the
    consequence is worse here: suppression that fails open leaves BOTH send verbs
    on the desk, which on a patrol turn is a desk whose own prompt forbids them.

    `test_every_module_disallow_signature_accepts_ctx_data` covers `module_registry`;
    this covers the case that guard cannot see — a module class that never
    reaches the map.
    """
    import logging

    class _Stale:

        # the base class composes the three tool hooks into the Assemble cell (batch 5c)

        contribute_tools = XYZBaseModule.contribute_tools
        config = SimpleNamespace(name="StaleModule", priority=5)

        async def mcp_server(self):
            return None

        async def disallowed_tools(self):  # the OLD signature, on purpose
            return ["mcp__stale__tool"]

        async def expressive_tools(self, ctx_data=None):
            return []

        async def contribute_turn_context(self, ctx_data) -> str:
            return ""

    instances = [
        SimpleNamespace(module_class="StaleModule", module=_Stale(), instance_id="i1")
    ]
    # loguru → stdlib, so caplog can see it. The point of the arm is that the
    # message is emitted at ERROR; a test that could not observe the level would
    # pass on a `warning` call and miss the whole distinction.
    from loguru import logger as _loguru

    handler_id = _loguru.add(
        lambda msg: logging.getLogger("desk_seam").error(msg), level="ERROR"
    )
    try:
        with caplog.at_level(logging.ERROR, logger="desk_seam"):
            await _collect(instances, monkeypatch)
    finally:
        _loguru.remove(handler_id)

    assert any(
        "signature mismatch" in r.getMessage() and "StaleModule" in r.getMessage()
        for r in caplog.records
    ), (
        "a stale disallowed_tools signature failed open with no ERROR — "
        f"records: {[r.getMessage() for r in caplog.records]}"
    )


@pytest.mark.asyncio
async def test_an_in_body_typeerror_is_not_reported_as_a_signature_mismatch(
    monkeypatch, caplog
):
    """The arm above cannot tell "the call was rejected" from "the body raised".

    Both fail open identically, so the only thing at stake is what the log says —
    and that is not nothing: on-call reads "signature mismatch", checks the
    override, finds it correct, and now has an ERROR line contradicting the code,
    while the real fault (suppression dropped, both send verbs on a patrol desk
    whose prompt forbids them) goes unlooked-at.

    A signature TypeError is raised while binding arguments, so it never enters the
    callee and its traceback has one frame. A body TypeError has more. That is the
    distinction, and this pins it from the observable side.
    """
    import logging

    class _RaisesInBody:

        # the base class composes the three tool hooks into the Assemble cell (batch 5c)

        contribute_tools = XYZBaseModule.contribute_tools
        config = SimpleNamespace(name="BodyRaiser", priority=5)

        async def mcp_server(self):
            return None

        async def disallowed_tools(self, ctx_data=None):
            # Correct signature; the BODY is wrong.
            return ["a"] * None  # type: ignore[operator]

        async def expressive_tools(self, ctx_data=None):
            return []

        async def contribute_turn_context(self, ctx_data) -> str:
            return ""

    instances = [
        SimpleNamespace(
            module_class="BodyRaiser", module=_RaisesInBody(), instance_id="i1"
        )
    ]
    from loguru import logger as _loguru

    seen: list[str] = []
    handler_id = _loguru.add(lambda msg: seen.append(str(msg)), level="ERROR")
    try:
        with caplog.at_level(logging.ERROR):
            await _collect(instances, monkeypatch)
    finally:
        _loguru.remove(handler_id)

    joined = " ".join(seen)
    assert "BodyRaiser" in joined, f"the failure was not logged at all: {seen}"
    assert "signature mismatch" not in joined, (
        "a TypeError from inside a correctly-shaped body was reported as a "
        f"signature mismatch: {joined}"
    )
    assert "raised" in joined, joined
