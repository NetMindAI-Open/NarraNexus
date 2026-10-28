"""
@file_name: test_cascade_stop_lineage.py
@date: 2026-08-07
@description: The trigger tree — how a stop reaches runs the owner never saw.

A long task rarely stays in one run: the agent asks a peer, that peer asks a
third. Stopping only the clicked run leaves those branches burning tokens and,
worse, their queued messages wake NEW runs — the owner presses stop and watches
fresh work appear.

The tree is a flat inherited LABEL (``events.root_run_id``), not parent/child
edges: the only question ever asked is "which runs belong to this tree", and a
label answers it in one indexed query at any depth.

Pinned here:
  * a root run labels itself; a caused run inherits
  * the label survives the agent→agent hop (the one place it could be lost:
    the message is written by an MCP tool in another process)
  * queued messages of a stopped tree stop waking runs
  * a NULL label is never treated as "same tree" (that would match every
    legacy row in the table)
"""
from __future__ import annotations

import pytest

from narranexus.platform.message_bus.local_bus import LocalMessageBus
from narranexus.platform.utils.timezone import utc_now


async def _seed_channel(db, channel_id="ch_1", agents=("agent_a", "agent_b")):
    await db.insert("bus_channels", {
        "channel_id": channel_id, "name": "room", "channel_type": "group",
        "created_by": "team_1",
    })
    for a in agents:
        await db.insert("bus_channel_members", {"channel_id": channel_id, "agent_id": a})
        await db.insert("agents", {"agent_id": a, "agent_name": a, "created_by": "user_x"})


async def _seed_run(db, event_id, *, root, state="running", cancel_at=None):
    await db.insert("events", {
        "event_id": event_id, "trigger": "message_bus", "trigger_source": "message_bus",
        "agent_id": "agent_a", "user_id": "user_x", "state": state,
        "started_at": utc_now(), "last_event_at": utc_now(),
        "root_run_id": root, "cancel_requested_at": cancel_at,
    })


# ── the label itself ────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_root_run_labels_itself(db_client):
    """No inherited root → the run IS a root and stamps its own id.

    Locked because the alternative (leaving it NULL for roots) would make the
    very first run of every tree unstoppable-as-a-tree.
    """
    from narranexus.platform.agent_runtime.run_recorder import RunRecorder

    await _seed_run(db_client, "evt_root", root=None, state="completed")
    rec = RunRecorder(db=db_client)
    await rec._bind_run_id("evt_root")
    try:
        row = await db_client.get_one("events", {"event_id": "evt_root"})
        assert row["root_run_id"] == "evt_root"
    finally:
        await rec.finalize("completed")


@pytest.mark.asyncio
async def test_a_caused_run_inherits_the_tree(db_client):
    from narranexus.platform.agent_runtime.run_recorder import RunRecorder

    await _seed_run(db_client, "evt_child", root=None, state="completed")
    rec = RunRecorder(db=db_client, inherited_root_run_id="evt_root")
    await rec._bind_run_id("evt_child")
    try:
        row = await db_client.get_one("events", {"event_id": "evt_child"})
        assert row["root_run_id"] == "evt_root"
    finally:
        await rec.finalize("completed")


@pytest.mark.asyncio
async def test_the_client_reads_the_tree_off_trigger_extra_data(db_client):
    """The seam between trigger and recorder — a rename on either side would
    silently orphan every caused run, and nothing else would fail."""
    from narranexus.platform.agent_runtime.client import _inherited_root_run_id

    assert _inherited_root_run_id({"trigger_extra_data": {"root_run_id": "evt_r"}}) == "evt_r"
    assert _inherited_root_run_id({"trigger_extra_data": {}}) is None
    assert _inherited_root_run_id({}) is None
    # A run that starts a tree passes "" — it must not be read as a tree named "".
    assert _inherited_root_run_id({"trigger_extra_data": {"root_run_id": ""}}) is None


# ── the hop where lineage could break ───────────────────────────────────────

@pytest.mark.asyncio
async def test_the_label_survives_the_agent_to_agent_hop(db_client):
    """An agent asking a peer writes a NEW message; the peer's run learns the
    tree only from that row. This is the one hop where the chain can break."""
    await _seed_channel(db_client)
    bus = LocalMessageBus(backend=db_client._backend)

    msg_id = await bus.send_message(
        from_agent="agent_a", to_channel="ch_1", content="@b help",
        root_run_id="evt_root",
    )

    row = await db_client.get_one("bus_messages", {"message_id": msg_id})
    assert row["root_run_id"] == "evt_root"
    # And it must come back out on the model the trigger reads.
    pending = await bus.get_pending_messages("agent_b")
    assert [m.root_run_id for m in pending] == ["evt_root"]


# ── queued work of a stopped tree ───────────────────────────────────────────

@pytest.mark.asyncio
async def test_queued_messages_of_a_stopped_tree_stop_waking_runs(db_client):
    """Stopping the running turns is not enough: their queued follow-ups would
    start new runs on the next poll, and the owner would see fresh work appear
    right after pressing stop."""
    await _seed_channel(db_client)
    await _seed_run(db_client, "evt_root", root="evt_root", cancel_at=utc_now())
    bus = LocalMessageBus(backend=db_client._backend)

    await bus.send_message(
        from_agent="agent_a", to_channel="ch_1", content="queued work",
        root_run_id="evt_root",
    )

    assert await bus.get_pending_messages("agent_b") == []


@pytest.mark.asyncio
async def test_a_live_tree_still_delivers(db_client):
    await _seed_channel(db_client)
    await _seed_run(db_client, "evt_root", root="evt_root", cancel_at=None)
    bus = LocalMessageBus(backend=db_client._backend)

    await bus.send_message(
        from_agent="agent_a", to_channel="ch_1", content="carry on",
        root_run_id="evt_root",
    )

    assert len(await bus.get_pending_messages("agent_b")) == 1


@pytest.mark.asyncio
async def test_an_unlabelled_message_is_never_suppressed(db_client):
    """A user's message and every pre-column row carry NULL. If NULL matched
    "the tree being stopped", one stop would mute the whole table."""
    await _seed_channel(db_client)
    await _seed_run(db_client, "evt_root", root="evt_root", cancel_at=utc_now())
    bus = LocalMessageBus(backend=db_client._backend)

    await bus.send_message(from_agent="agent_a", to_channel="ch_1", content="hello")

    pending = await bus.get_pending_messages("agent_b")
    assert len(pending) == 1
    assert pending[0].root_run_id is None


@pytest.mark.asyncio
async def test_a_stopped_tree_whose_root_already_finished_still_suppresses(db_client):
    """The delegating shape: the root run ENDS before the work does.

    An agent that hands work to a peer typically finishes its own turn right
    after sending — "fire it off, end my turn, they'll answer later". So by the
    time the owner sees the peer running and presses stop, the ROOT row is
    already `completed` and never receives a flag (settled rows are
    deliberately not flagged — a terminal row must not carry a pending stop).

    A suppression predicate that only looks at the root row therefore reads
    "nothing was stopped" and keeps waking new runs — the whack-a-mole this
    feature exists to kill, in its most common form. The question the filter
    must ask is "did ANYONE in this tree get stopped", not "did the root".
    """
    await _seed_channel(db_client)
    # Root finished long ago, carries no flag.
    await _seed_run(db_client, "evt_root", root="evt_root", state="completed")
    # Its child is the one still running, and the one the owner stopped.
    await _seed_run(db_client, "evt_child", root="evt_root", cancel_at=utc_now())
    bus = LocalMessageBus(backend=db_client._backend)

    await bus.send_message(
        from_agent="agent_a", to_channel="ch_1", content="queued follow-up",
        root_run_id="evt_root",
    )

    assert await bus.get_pending_messages("agent_b") == []


# ── the whole chain, from a ROOT turn's header to a two-hop stop ─────────────
#
# Everything above hands the label in by hand ("evt_root"). The one place the
# label is DERIVED is `ContextRuntime.build_input_for_framework`: a root turn
# has nothing upstream to inherit, so it must stamp its own event id into the
# MCP identity header (B-19, GitHub #124). Before that fix the header was ""
# for every user-triggered turn, the message it sent carried NULL, and the
# run that message woke labelled itself a fresh root — so the cascade only
# ever reached job-seeded trees. This test walks the real links:
#
#   root turn's header → message_agent (the registered tool, ambient headers)
#   → bus_messages.root_run_id → the trigger's trigger_extra_data →
#   client._inherited_root_run_id → RunRecorder._bind_run_id →
#   events.root_run_id on the woken run → POST /runs/{root}/cancel hits both.
#
# Nothing on that path is mocked; deleting the `or event_id` fallback in
# context_runtime turns this red.

ROOT_RUN = "evt_root_turn"
CHILD_RUN = "evt_child_turn"


async def _root_turn_headers(agent_id: str, event_id: str) -> dict:
    """The MCP identity headers a ROOT turn hands its module servers —
    built by the real ContextRuntime, with a trigger that carries no tree
    (exactly what a user's message produces: `root_run_id=""`)."""
    from types import SimpleNamespace

    from narranexus.platform.context_runtime.context_runtime import ContextRuntime
    from narranexus.platform.module_system.base import XYZBaseModule
    from narranexus.platform.schema import ContextData, WorkingSource

    class _BusModule:
        contribute_tools = XYZBaseModule.contribute_tools

        async def mcp_server(self):
            return SimpleNamespace(server_name="message_bus_module", server_url="http://x/sse")

        async def contribute_turn_context(self, ctx_data):
            return ""

        async def expressive_tools(self, ctx_data=None):
            return []

    runtime = ContextRuntime(agent_id=agent_id, user_id="user_x", database_client=object(), event_id=event_id)
    ctx = ContextData(agent_id=agent_id, input_content="hi")
    ctx.working_source = WorkingSource.MESSAGE_BUS
    ctx.extra_data = {"bus_channel_id": "ch_1", "root_run_id": ""}
    _m, servers, *_r = await runtime.build_input_for_framework(
        messages=[], system_prompt="sys", ctx_data=ctx,
        active_instances=[SimpleNamespace(module_class="MessageBusModule", instance_id="mb_1", module=_BusModule())],
    )
    return servers["message_bus_module"]["headers"]


def _registered_bus_tools(bus) -> dict:
    from narranexus_plugins.message_bus_module._message_bus_mcp_tools import register_message_bus_mcp_tools

    captured: dict = {}

    class _MCP:
        def tool(self, *_a, **_k):
            def deco(fn):
                captured[fn.__name__] = fn
                return fn
            return deco

    async def _get_bus():
        return bus

    register_message_bus_mcp_tools(_MCP(), _get_bus)
    return captured


@pytest.mark.asyncio
async def test_a_root_turns_send_puts_the_woken_run_in_its_tree_and_one_stop_reaches_both(db_client, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import backend.routes.runs as runs_mod
    from narranexus.platform.agent_runtime.client import _inherited_root_run_id
    from narranexus.platform.agent_runtime.run_recorder import RunRecorder

    from ._mcp_headers import injected

    await _seed_channel(db_client)
    for name in ("narranexus.platform.utils.db.db_factory.get_db_client", "narranexus.platform.utils.get_db_client"):
        monkeypatch.setattr(name, _return(db_client))
    bus = LocalMessageBus(backend=db_client._backend)

    # 1. The root run: nothing inherited, so the recorder labels it with itself.
    await _seed_run(db_client, ROOT_RUN, root=None, state="completed")
    root_rec = RunRecorder(db=db_client, inherited_root_run_id=_inherited_root_run_id({"trigger_extra_data": {"root_run_id": ""}}))
    await root_rec._bind_run_id(ROOT_RUN)
    assert (await db_client.get_one("events", {"event_id": ROOT_RUN}))["root_run_id"] == ROOT_RUN

    # 2. Inside that turn, the agent asks a peer through the real tool, under
    #    the headers the real ContextRuntime built for a root turn.
    headers = await _root_turn_headers("agent_a", ROOT_RUN)
    with injected(headers):
        result = await _registered_bus_tools(bus)["message_agent"](agent_id="agent_a", to="agent_b", text="@b help")
    assert result["success"] is True

    # 3. The message carries the tree; the trigger forwards it as the woken
    #    run's trigger_extra_data (message_bus_trigger: `root_run_id or ""`).
    pending = await bus.get_pending_messages("agent_b")
    assert [m.root_run_id for m in pending] == [ROOT_RUN]
    woken_extra = {"trigger_extra_data": {"root_run_id": pending[0].root_run_id or ""}}

    # 4. The woken run inherits the label through the client's seam.
    await _seed_run(db_client, CHILD_RUN, root=None, state="completed")
    child_rec = RunRecorder(db=db_client, inherited_root_run_id=_inherited_root_run_id(woken_extra))
    await child_rec._bind_run_id(CHILD_RUN)
    try:
        assert (await db_client.get_one("events", {"event_id": CHILD_RUN}))["root_run_id"] == ROOT_RUN

        # 5. One stop on the ROOT reaches the run it caused, two hops away.
        app = FastAPI()
        app.include_router(runs_mod.router, prefix="/api/runs")

        @app.middleware("http")
        async def _auth(request, call_next):
            request.state.user_id = "user_x"
            return await call_next(request)

        monkeypatch.setattr(runs_mod, "get_db_client", _return(db_client))
        resp = TestClient(app).post(f"/api/runs/{ROOT_RUN}/cancel")
        assert resp.status_code == 200, resp.text
        for run_id in (ROOT_RUN, CHILD_RUN):
            assert (await db_client.get_one("events", {"event_id": run_id}))["cancel_requested_at"] is not None

        # ...and the stopped tree's queued follow-ups stop waking runs.
        with injected(headers):
            await _registered_bus_tools(bus)["message_agent"](agent_id="agent_a", to="agent_b", text="one more thing")
        assert await bus.get_pending_messages("agent_b") == []
    finally:
        await root_rec.finalize("cancelled")
        await child_rec.finalize("cancelled")


def _return(value):
    async def _get():
        return value
    return _get
