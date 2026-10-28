"""
@file_name: test_job_origin_and_identity.py
@author:
@date: 2026-08-14
@description: A job created in a team room reports back INTO that room, under
              the owner's real identity.

Two defects with one cause: a job remembered WHAT to do and forgot WHERE it was
asked.

**The reply surface.** `job_module` registers exactly one reply tool
(`send_message_to_user_directly`) and the execution prompt hard-codes "the
owner will see it when they open this conversation". So "@Leader remind us
tomorrow morning" — asked in a team room, in front of four people — delivered
into the owner's private chat. The room that asked never heard back. This is
PR #230's "the reply follows where it came from" applied to the job surface.

**The identity.** A bus turn runs with `user_id = sender_agent_id`
(`message_bus_trigger._invoke_runtime`), because on that surface "the user
whose request this is" is a peer. That value reached `job.user_id`, so a job
asked for in a team room was filed under `usr_<uid>` or a peer's `agent_id` —
an owner that does not exist. The owner's Jobs list stayed empty while the
agent reported success.

`_mcp_identity.resolve_caller_user_id` deliberately does NOT fix this: it
overrides placeholders only, and its comment says a mismatching REAL value can
be legitimate in multi-user flows. That judgement is about the generic identity
path and is left alone. A job's owner is a narrower question with a ground
truth — `agents.created_by` — so it is answered at the write site.

The regression half matters as much: a job asked for in private chat must
behave exactly as before, both in where it reports and in whose name it runs
(PRD acceptance #8).
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from narranexus_plugins.job_module.job_service import JobInstanceService
from narranexus_plugins.job_module._job_writes import create_job_from_args
from narranexus.platform.schema.job_schema import JobOrigin


AGENT = "agent_worker"
OWNER = "usr_real_owner"
ROOM = "ch_team_room"
TEAM = "t_desk"
# What a bus turn actually puts on the wire as "user_id".
BUS_SENDER = "usr_someone"
PEER_AGENT = "agent_peer"


# `setup_mcp_llm_context` is imported INSIDE create_job_from_args, so it is
# patched at its source module (the same seam test_job_mcp_tool_hardening uses).
# It loads the owner's provider config, which no fixture here has — and which is
# not what any of these assertions are about.
API_MOD = "narranexus.platform.agent_framework.api_config"


@pytest.fixture(autouse=True)
def _no_llm_config():
    with patch(f"{API_MOD}.setup_mcp_llm_context", AsyncMock()):
        yield


async def _seed_agent(db, agent_id=AGENT, owner=OWNER):
    await db.insert(
        "agents", {"agent_id": agent_id, "agent_name": "W", "created_by": owner}
    )


# ===========================================================================
# Origin — where the answer goes
# ===========================================================================

@pytest.mark.asyncio
async def test_a_job_created_in_a_room_remembers_the_room(db_client):
    await _seed_agent(db_client)
    service = JobInstanceService(db_client)

    result = await service.create_job_with_instance(
        agent_id=AGENT, user_id=OWNER, title="morning reminder",
        description="d", job_type="scheduled",
        trigger_config={"cron": "0 8 * * *", "timezone": "Asia/Shanghai"},
        payload="remind the team",
        origin_source=JobOrigin.MESSAGE_BUS, origin_channel_id=ROOM,
    )

    assert result["success"], result
    row = await db_client.get_one("instance_jobs", {"job_id": result["job_id"]})
    assert row["origin_source"] == JobOrigin.MESSAGE_BUS
    assert row["origin_channel_id"] == ROOM


@pytest.mark.asyncio
async def test_a_private_chat_job_records_no_origin(db_client):
    """The default path must stay exactly what it was (acceptance #8).

    An empty origin is what makes "report to the owner" the fallback rather
    than a special case — a job with nowhere else to go has one place to go.
    """
    await _seed_agent(db_client)
    service = JobInstanceService(db_client)

    result = await service.create_job_with_instance(
        agent_id=AGENT, user_id=OWNER, title="t", description="d",
        job_type="scheduled",
        trigger_config={"cron": "0 8 * * *", "timezone": "Asia/Shanghai"},
        payload="p",
    )

    row = await db_client.get_one("instance_jobs", {"job_id": result["job_id"]})
    assert not row["origin_source"]
    assert not row["origin_channel_id"]


# ===========================================================================
# Identity — whose job it is
# ===========================================================================

@pytest.mark.asyncio
async def test_a_bus_senders_id_never_becomes_the_jobs_owner(db_client):
    """The founding defect: `usr_<uid>` / a peer agent id filed as the owner.

    Asserted through `create_job_from_args` because that is the shared write
    path both the local MCP process and the cloud seam route go through — a fix
    one layer up would miss whichever caller was not patched.
    """
    await _seed_agent(db_client)

    result = await create_job_from_args(
        db_client, AGENT,
        user_id=BUS_SENDER,  # what a bus turn hands the tool
        title="t", description="d", job_type="scheduled",
        trigger_config={"cron": "0 8 * * *", "timezone": "Asia/Shanghai"},
        payload="p",
    )

    assert result.get("success"), result
    row = await db_client.get_one("instance_jobs", {"job_id": result["job_id"]})
    assert row["user_id"] == OWNER
    assert not row["user_id"].startswith("agent_")


@pytest.mark.asyncio
async def test_a_peer_agent_id_is_corrected_too(db_client):
    """An A2A turn hands the peer's agent_id, which looks nothing like a user
    and would never have loaded any context at execution."""
    await _seed_agent(db_client)

    result = await create_job_from_args(
        db_client, AGENT, user_id=PEER_AGENT,
        title="t", description="d", job_type="scheduled",
        trigger_config={"cron": "0 8 * * *", "timezone": "Asia/Shanghai"},
        payload="p",
    )

    row = await db_client.get_one("instance_jobs", {"job_id": result["job_id"]})
    assert row["user_id"] == OWNER


@pytest.mark.asyncio
async def test_an_unresolvable_owner_leaves_the_supplied_id_alone(db_client):
    """Fail open. An agent row that cannot be read is not evidence that the
    caller was wrong, and blanking the field would lose the job entirely."""
    result = await create_job_from_args(
        db_client, "agent_with_no_row", user_id="usr_supplied",
        title="t", description="d", job_type="scheduled",
        trigger_config={"cron": "0 8 * * *", "timezone": "Asia/Shanghai"},
        payload="p",
    )

    row = await db_client.get_one("instance_jobs", {"job_id": result["job_id"]})
    assert row["user_id"] == "usr_supplied"


@pytest.mark.asyncio
async def test_the_target_entity_is_not_touched(db_client):
    """`related_entity_id` answers "about whom", `user_id` answers "whose job".

    Collapsing them is how "a job acting on another user" would break — that
    shape is supported and must keep working.
    """
    await _seed_agent(db_client)

    result = await create_job_from_args(
        db_client, AGENT, user_id=BUS_SENDER,
        title="t", description="d", job_type="scheduled",
        trigger_config={"cron": "0 8 * * *", "timezone": "Asia/Shanghai"},
        payload="p", related_entity_id="usr_other_person",
    )

    row = await db_client.get_one("instance_jobs", {"job_id": result["job_id"]})
    assert row["user_id"] == OWNER
    assert row["related_entity_id"] == "usr_other_person"


# ===========================================================================
# Execution — the prompt must match the surface it will actually deliver on
# ===========================================================================

def test_a_room_job_is_told_its_reply_goes_to_the_room():
    """The prompt and the delivery have to agree.

    Naming the owner's tool in a room-origin job is how the answer ended up in
    one person's private chat while the room that asked heard nothing. One
    template per surface, chosen by the recorded origin.

    Both templates changed on 2026-08-17 and the room one changed in substance,
    not wording: it used to say the reply auto-posts and no function was needed.
    That was true only while the team room accepted plain text — the single
    surface where "plain text reaches nobody" was false. It is a tool call now,
    so an unchanged room prompt would have told a job to write its report and
    stop, and the room would have received nothing at all. The assertions below
    pin the tool NAMES rather than prose, because a name is what the agent has
    to be able to call.
    """
    from narranexus_plugins.job_module.prompts import (
        JOB_DELIVERY_TO_OWNER,
        JOB_DELIVERY_TO_ROOM,
        job_delivery_instructions,
    )

    room = job_delivery_instructions(JobOrigin.MESSAGE_BUS)
    private = job_delivery_instructions("")

    assert room is JOB_DELIVERY_TO_ROOM
    assert private is JOB_DELIVERY_TO_OWNER
    # Each names its own surface's tool, and NEITHER names the other's — a
    # template that mentioned both would leave the choice to the model, which
    # is the failure the split exists to remove.
    assert "message_team" in room and "notify_owner" not in room
    assert "notify_owner" in private and "message_team" not in private
    # The retired name must not survive anywhere: it would point the job at a
    # tool that no longer exists on any desk.
    assert "send_message_to_user_directly" not in room + private
    # And the room prompt must not go back to promising an auto-post.
    assert "automatically" not in room
    # An unknown origin degrades to the surface that always exists.
    assert job_delivery_instructions("carrier_pigeon") is JOB_DELIVERY_TO_OWNER


@pytest.mark.asyncio
async def test_the_report_lands_in_the_room_that_asked(db_client, monkeypatch):
    """Acceptance #4, end to end on the delivery side."""
    from narranexus_plugins.job_module.job_trigger import JobTrigger
    from narranexus.platform.schema.job_schema import JobModel, TriggerConfig

    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _async_db
    )
    await db_client.insert(
        "bus_channels",
        {"channel_id": ROOM, "name": "Desk", "channel_type": "group",
         "created_by": f"team_{TEAM}"},
    )

    job = JobModel(
        job_id="job_1", agent_id=AGENT, user_id=OWNER, title="t", description="d",
        job_type="one_off",
        trigger_config=TriggerConfig(run_at="2026-08-15T08:00:00",
                                     timezone="Asia/Shanghai"),
        payload="p", origin_source=JobOrigin.MESSAGE_BUS, origin_channel_id=ROOM,
    )

    await JobTrigger.__new__(JobTrigger)._deliver_to_origin(job, "morning report")

    rows = await db_client.get("bus_messages", {"channel_id": ROOM})
    assert [r["content"] for r in rows] == ["morning report"]
    assert rows[0]["from_agent"] == AGENT


@pytest.mark.asyncio
async def test_an_owner_chat_job_posts_nowhere(db_client, monkeypatch):
    """The regression half of acceptance #8: no origin, no room write.

    A job that reports through `send_message_to_user_directly` during its run
    must not ALSO get a platform post, or every private reminder would grow a
    duplicate somewhere.
    """
    from narranexus_plugins.job_module.job_trigger import JobTrigger
    from narranexus.platform.schema.job_schema import JobModel, TriggerConfig

    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _async_db
    )

    job = JobModel(
        job_id="job_2", agent_id=AGENT, user_id=OWNER, title="t", description="d",
        job_type="one_off",
        trigger_config=TriggerConfig(run_at="2026-08-15T08:00:00",
                                     timezone="Asia/Shanghai"),
        payload="p",
    )

    await JobTrigger.__new__(JobTrigger)._deliver_to_origin(job, "private report")

    assert await db_client.get("bus_messages", {}) == []


@pytest.mark.asyncio
async def test_the_report_carries_its_provenance(db_client, monkeypatch):
    """A job report is the THIRD way an agent's words enter a room.

    The other two (a live reply, a patrol line) both stamp `event_id`, which is
    what the room transcript reads to offer "view reasoning & tools", and
    `root_run_id`, which is what a cascade stop follows. Nobody in the room
    watched this turn happen, so an unstamped line is a piece of text with no
    way back to what produced it.
    """
    from narranexus_plugins.job_module.job_trigger import JobTrigger
    from narranexus.platform.schema.job_schema import JobModel, TriggerConfig

    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _async_db
    )

    job = JobModel(
        job_id="job_4", agent_id=AGENT, user_id=OWNER, title="t", description="d",
        job_type="one_off",
        trigger_config=TriggerConfig(run_at="2026-08-18T08:00:00",
                                     timezone="Asia/Shanghai"),
        payload="p", origin_source=JobOrigin.MESSAGE_BUS, origin_channel_id=ROOM,
    )

    await JobTrigger.__new__(JobTrigger)._deliver_to_origin(
        job, "the report", run_event_id="evt_real_run",
    )

    rows = await db_client.get("bus_messages", {"channel_id": ROOM})
    assert rows[0]["event_id"] == "evt_real_run"
    # A job run has no parent — a timer woke it — so it roots its own tree.
    assert rows[0]["root_run_id"] == "evt_real_run"
    # A report is a notice, not a request: an @ would wake a team turn AND
    # open an errand for a hand-off nobody made.
    assert not rows[0]["mentions"]


@pytest.mark.asyncio
async def test_a_failed_room_job_says_so_in_the_room(db_client, monkeypatch):
    """Silence is the failure mode this whole change exists to remove.

    The owner-chat path has places to surface a failure (the Jobs panel,
    `job.last_error`); a room has none. Four people watched someone ask for the
    reminder, and without this they simply never hear about it again — the same
    broken hand-off, one surface over.
    """
    from narranexus_plugins.job_module.job_trigger import JobTrigger
    from narranexus.platform.schema.job_schema import JobModel, TriggerConfig

    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _async_db
    )

    job = JobModel(
        job_id="job_5", agent_id=AGENT, user_id=OWNER, title="t", description="d",
        job_type="one_off",
        trigger_config=TriggerConfig(run_at="2026-08-18T08:00:00",
                                     timezone="Asia/Shanghai"),
        payload="p", origin_source=JobOrigin.MESSAGE_BUS, origin_channel_id=ROOM,
    )

    await JobTrigger.__new__(JobTrigger)._deliver_to_origin(
        job, "⚠️ Scheduled task failed: provider timed out",
    )

    rows = await db_client.get("bus_messages", {"channel_id": ROOM})
    assert "Scheduled task failed" in rows[0]["content"]


@pytest.mark.asyncio
async def test_an_empty_run_does_not_put_a_metadata_block_in_the_room(
    db_client, monkeypatch
):
    """The boilerplate is written for an inbox, and a room is not an inbox.

    When a run produces nothing, `_run_agent` synthesises "## Task Completed …
    Job ID … Tools used: None" — a useful operational record in the owner's
    chat. Posted into a room it puts a platform-shaped notice in front of four
    people and reads as a bug rather than as "the job had nothing to say".

    Asserted through `_run_agent` rather than `_deliver_to_origin`, because the
    thing under test is WHICH text reaches the room — the ordering of the two
    statements, not the delivery itself. The owner path keeps the boilerplate
    (PRD acceptance #8), which the return value below pins.
    """
    from narranexus.platform.agent_runtime.run_collector import RunCollection
    from narranexus_plugins.job_module.job_trigger import JobTrigger
    from narranexus.platform.schema.job_schema import JobModel, TriggerConfig

    async def _async_db():
        return db_client

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _async_db
    )

    job = JobModel(
        job_id="job_6", agent_id=AGENT, user_id=OWNER, title="Morning digest",
        description="d", job_type="one_off",
        trigger_config=TriggerConfig(run_at="2026-08-18T08:00:00",
                                     timezone="Asia/Shanghai"),
        payload="p", origin_source=JobOrigin.MESSAGE_BUS, origin_channel_id=ROOM,
    )

    class _Client:
        async def run_and_collect(self, **_k):
            return RunCollection(output_text="", tool_calls=[], event_id="evt_x")

    monkeypatch.setattr(
        "narranexus_plugins.job_module.job_trigger."
        "get_agent_runtime_client",
        lambda: _Client(),
    )

    trigger = JobTrigger.__new__(JobTrigger)
    result = await trigger._run_agent(job, "do the thing")

    assert await db_client.get("bus_messages", {"channel_id": ROOM}) == [], (
        "the room got the inbox's operational boilerplate"
    )
    # The owner-facing record is unchanged.
    assert "Task Completed" in result["content"]
    assert job.job_id in result["content"]


@pytest.mark.asyncio
async def test_an_undeliverable_report_does_not_fail_the_job(db_client, monkeypatch):
    """The job succeeded — its status and next_run_time are already correct.

    Letting the post's failure propagate would rewrite a completed job as a
    failed one and re-arm it, so the same work runs again for a bookkeeping
    error.
    """
    from narranexus_plugins.job_module.job_trigger import JobTrigger
    from narranexus.platform.schema.job_schema import JobModel, TriggerConfig

    async def _boom():
        raise RuntimeError("db is down")

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _boom
    )

    job = JobModel(
        job_id="job_3", agent_id=AGENT, user_id=OWNER, title="t", description="d",
        job_type="one_off",
        trigger_config=TriggerConfig(run_at="2026-08-15T08:00:00",
                                     timezone="Asia/Shanghai"),
        payload="p", origin_source=JobOrigin.MESSAGE_BUS, origin_channel_id=ROOM,
    )

    # No raise is the assertion.
    await JobTrigger.__new__(JobTrigger)._deliver_to_origin(job, "report")


@pytest.mark.asyncio
async def test_a_long_report_reaches_the_room_in_parts_not_dropped(db_client, monkeypatch):
    """The bus refuses one row over MAX_BUS_MESSAGE_BYTES; the fallback must
    not let that become a silent `logger.error` (review r2 I2). A 70 KB report
    goes as two ordered parts the room's lane reassembles."""
    from narranexus_plugins.job_module.job_trigger import JobTrigger
    from narranexus.platform.message_bus import multipart
    from narranexus.platform.schema.job_schema import JobModel, TriggerConfig

    async def _async_db():
        return db_client

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", _async_db)
    await db_client.insert(
        "bus_channels",
        {"channel_id": ROOM, "name": "Desk", "channel_type": "group", "created_by": f"team_{TEAM}"},
    )
    job = JobModel(
        job_id="job_long", agent_id=AGENT, user_id=OWNER, title="t", description="d",
        job_type="one_off",
        trigger_config=TriggerConfig(run_at="2026-08-15T08:00:00", timezone="Asia/Shanghai"),
        payload="p", origin_source=JobOrigin.MESSAGE_BUS, origin_channel_id=ROOM,
    )
    report = "r" * (multipart.MAX_BUS_MESSAGE_BYTES + 10_000)

    await JobTrigger.__new__(JobTrigger)._deliver_to_origin(job, report)

    rows = sorted(await db_client.get("bus_messages", {"channel_id": ROOM}), key=lambda r: r["part_index"])
    assert [(r["part_index"], r["part_count"]) for r in rows] == [(1, 2), (2, 2)]
    assert "".join(r["content"] for r in rows) == report
    whole, held = multipart.assemble([
        __import__("narranexus.platform.message_bus.local_bus", fromlist=["LocalMessageBus"])
        .LocalMessageBus(backend=db_client._backend)._row_to_message(r) for r in rows
    ])
    assert held is False and whole[0].content == report


@pytest.mark.asyncio
async def test_a_report_past_the_whole_message_budget_tells_the_room(db_client, monkeypatch):
    from narranexus_plugins.job_module.job_trigger import JobTrigger
    from narranexus.platform.message_bus import multipart
    from narranexus.platform.schema.job_schema import JobModel, TriggerConfig

    async def _async_db():
        return db_client

    monkeypatch.setattr("narranexus.platform.utils.db.db_factory.get_db_client", _async_db)
    await db_client.insert(
        "bus_channels",
        {"channel_id": ROOM, "name": "Desk", "channel_type": "group", "created_by": f"team_{TEAM}"},
    )
    job = JobModel(
        job_id="job_huge", agent_id=AGENT, user_id=OWNER, title="t", description="d",
        job_type="one_off",
        trigger_config=TriggerConfig(run_at="2026-08-15T08:00:00", timezone="Asia/Shanghai"),
        payload="p", origin_source=JobOrigin.MESSAGE_BUS, origin_channel_id=ROOM,
    )

    await JobTrigger.__new__(JobTrigger)._deliver_to_origin(job, "h" * (multipart.MAX_MULTIPART_TOTAL_BYTES + 1))

    rows = await db_client.get("bus_messages", {"channel_id": ROOM})
    assert len(rows) == 1 and "was not posted here" in rows[0]["content"]
