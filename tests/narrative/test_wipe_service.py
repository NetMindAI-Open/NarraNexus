"""
@file_name: test_wipe_service.py
@author: NarraNexus
@date: 2026-07-10
@description: Tests for wipe_agent_data — the full "clear conversation &
memory" service behind DELETE /api/agents/{id}/history.

The pre-existing clear-history path only touched a few DB tables and never
the on-disk narrative markdown / trajectories, so agents kept remembering
after a "clear" (the DB was rebuilt from disk on restart). These tests pin
the correct behaviour for the three scope combinations and the two hard
invariants: cross-agent isolation (memory_* is agent-scoped) and preservation
of the agent's identity (persona / system instances).
"""
from __future__ import annotations

import json
import os

import pytest

from xyz_agent_context.narrative.wipe_service import wipe_agent_data
from xyz_agent_context.narrative.session_service import SessionService
from xyz_agent_context.utils.schema_registry import MEMORY_KINDS


TARGET = "agent_target"
OTHER = "agent_other"
USER = "user_x"


def _actors(user_id: str) -> str:
    return json.dumps({"name": "N", "description": "d", "actors": [{"id": user_id}]})


async def _seed(db, *, md_base: str, traj_base: str, sessions: SessionService) -> dict:
    """Seed two agents' worth of conversation + memory, in DB and on disk.

    Returns a dict of the seeded ids for assertions.
    """
    # --- agents ---
    await db.insert("agents", {"agent_id": TARGET, "agent_name": "T", "created_by": USER})
    await db.insert("agents", {"agent_id": OTHER, "agent_name": "O", "created_by": USER})

    # --- narratives (target: N1, N2 for USER; other: Nb) ---
    for nid in ("N1", "N2"):
        await db.insert("narratives", {
            "narrative_id": nid, "type": "other", "agent_id": TARGET,
            "narrative_info": _actors(USER),
        })
    await db.insert("narratives", {
        "narrative_id": "Nb", "type": "other", "agent_id": OTHER,
        "narrative_info": _actors(USER),
    })

    # --- events + event_stream ---
    for eid, nid in (("e1", "N1"), ("e2", "N2")):
        await db.insert("events", {
            "event_id": eid, "trigger": "chat", "trigger_source": "chat",
            "narrative_id": nid, "agent_id": TARGET, "user_id": USER,
        })
        await db.insert("event_stream", {"event_id": eid, "seq": 0, "kind": "delta", "payload": "x"})
    # other agent event must survive
    await db.insert("events", {
        "event_id": "eb", "trigger": "chat", "trigger_source": "chat",
        "narrative_id": "Nb", "agent_id": OTHER, "user_id": USER,
    })
    await db.insert("event_stream", {"event_id": "eb", "seq": 0, "kind": "delta", "payload": "x"})

    # --- module_instances: system (preserve) + chat (delete) ---
    await db.insert("module_instances", {
        "instance_id": "aware_1", "module_class": "AwarenessModule",
        "agent_id": TARGET, "user_id": None, "is_public": 1, "status": "active",
    })
    await db.insert("module_instances", {
        "instance_id": "skill_default", "module_class": "SkillModule",
        "agent_id": TARGET, "user_id": None, "is_public": 1, "status": "active",
    })
    for iid in ("chat_1", "chat_2"):
        await db.insert("module_instances", {
            "instance_id": iid, "module_class": "ChatModule",
            "agent_id": TARGET, "user_id": USER, "is_public": 0, "status": "active",
        })
        await db.insert("instance_json_format_memory_chat", {
            "instance_id": iid, "memory": "[]",
        })

    # --- links + report memory + agent_messages ---
    await db.insert("instance_narrative_links", {"instance_id": "chat_1", "narrative_id": "N1"})
    await db.insert("instance_narrative_links", {"instance_id": "aware_1", "narrative_id": "N1"})
    await db.insert("module_report_memory", {
        "narrative_id": "N1", "module_name": "chat", "report_memory": "{}",
    })
    await db.insert("agent_messages", {
        "message_id": "am1", "agent_id": TARGET, "source_type": "chat",
        "source_id": "s", "content": "hi",
    })

    # --- MessageBus: a private (sole-member) channel + a shared channel ---
    await db.insert("bus_channels", {"channel_id": "dm", "name": "dm", "created_by": TARGET})
    await db.insert("bus_channel_members", {"channel_id": "dm", "agent_id": TARGET})
    await db.insert("bus_messages", {
        "message_id": "bm_dm", "channel_id": "dm", "from_agent": "user", "content": "私聊历史",
    })
    await db.insert("bus_channels", {"channel_id": "shared", "name": "shared", "created_by": TARGET})
    await db.insert("bus_channel_members", {"channel_id": "shared", "agent_id": TARGET})
    await db.insert("bus_channel_members", {"channel_id": "shared", "agent_id": OTHER})
    await db.insert("bus_messages", {
        "message_id": "bm_shared", "channel_id": "shared", "from_agent": "user", "content": "群聊历史",
    })

    # --- memory_* (one row per kind for each agent) + consolidation queue + artifacts ---
    for kind in MEMORY_KINDS:
        await db.insert(f"memory_{kind}", {
            "record_id": f"{TARGET}_{kind}", "agent_id": TARGET,
            "scope_type": "agent", "kind": kind,
        })
        await db.insert(f"memory_{kind}", {
            "record_id": f"{OTHER}_{kind}", "agent_id": OTHER,
            "scope_type": "agent", "kind": kind,
        })
    await db.insert("memory_consolidation_queue", {
        "agent_id": TARGET, "scope_type": "agent", "scope_id": "", "kind": "event",
    })
    await db.insert("instance_artifacts", {
        "artifact_id": "art_t", "agent_id": TARGET, "user_id": USER,
        "title": "t", "kind": "file",
    })
    await db.insert("instance_artifacts", {
        "artifact_id": "art_o", "agent_id": OTHER, "user_id": USER,
        "title": "o", "kind": "file",
    })

    # --- disk: narrative md/stats + trajectory + session (target only) ---
    nar_dir = os.path.join(md_base, TARGET, USER, "narratives")
    os.makedirs(nar_dir, exist_ok=True)
    with open(os.path.join(nar_dir, "N1.md"), "w") as f:
        f.write("# mem")
    with open(os.path.join(nar_dir, "N1_stats.json"), "w") as f:
        f.write("{}")
    traj_dir = os.path.join(traj_base, TARGET, USER, "trajectories", "N1")
    os.makedirs(traj_dir, exist_ok=True)
    with open(os.path.join(traj_dir, "round_001.json"), "w") as f:
        f.write("{}")
    # other agent disk must survive
    other_nar = os.path.join(md_base, OTHER, USER, "narratives")
    os.makedirs(other_nar, exist_ok=True)
    with open(os.path.join(other_nar, "Nb.md"), "w") as f:
        f.write("# keep")
    # session file
    sess_file = sessions._get_session_file_path(TARGET, USER)
    with open(sess_file, "w") as f:
        f.write("{}")

    return {
        "nar_dir": os.path.join(md_base, TARGET, USER, "narratives"),
        "traj_dir": os.path.join(traj_base, TARGET, USER, "trajectories"),
        "other_nar": other_nar,
        "sess_file": str(sess_file),
    }


async def _count(db, table, filters):
    return len(await db.get(table, filters))


@pytest.fixture
def bases(tmp_path):
    md = str(tmp_path / "narratives")
    traj = str(tmp_path / "trajectories")
    sessions = SessionService(session_dir=str(tmp_path / "sessions"))
    return md, traj, sessions


@pytest.mark.asyncio
async def test_conversations_only(db_client, bases):
    md, traj, sessions = bases
    paths = await _seed(db_client, md_base=md, traj_base=traj, sessions=sessions)

    result = await wipe_agent_data(
        db_client, TARGET, USER,
        clear_conversations=True, clear_memory=False,
        narrative_base=md, trajectory_base=traj, session_service=sessions,
    )

    # conversation gone
    assert await _count(db_client, "narratives", {"agent_id": TARGET}) == 0
    assert await _count(db_client, "events", {"agent_id": TARGET}) == 0
    assert await _count(db_client, "event_stream", {"event_id": "e1"}) == 0
    assert await _count(db_client, "instance_json_format_memory_chat", {"instance_id": "chat_1"}) == 0
    assert await _count(db_client, "agent_messages", {"agent_id": TARGET}) == 0
    # chat instances gone, system instances preserved
    assert await _count(db_client, "module_instances", {"instance_id": "chat_1"}) == 0
    assert await _count(db_client, "module_instances", {"instance_id": "aware_1"}) == 1
    assert await _count(db_client, "module_instances", {"instance_id": "skill_default"}) == 1
    # memory preserved (this scope does not touch it)
    for kind in MEMORY_KINDS:
        assert await _count(db_client, f"memory_{kind}", {"agent_id": TARGET}) == 1
    assert await _count(db_client, "instance_artifacts", {"agent_id": TARGET}) == 1
    # MessageBus: sole-member (private) channel history wiped; shared kept
    assert await _count(db_client, "bus_messages", {"channel_id": "dm"}) == 0
    assert await _count(db_client, "bus_messages", {"channel_id": "shared"}) == 1
    # channel bindings preserved (agent keeps receiving new IM)
    assert await _count(db_client, "bus_channel_members", {"agent_id": TARGET}) == 2
    # disk conversation gone
    assert not os.path.exists(paths["nar_dir"])
    assert not os.path.exists(paths["traj_dir"])
    assert not os.path.exists(paths["sess_file"])
    # other agent fully intact
    assert await _count(db_client, "narratives", {"agent_id": OTHER}) == 1
    assert await _count(db_client, "event_stream", {"event_id": "eb"}) == 1
    assert os.path.exists(paths["other_nar"])

    assert result.narratives_count == 2
    assert result.chat_instances_count == 2


@pytest.mark.asyncio
async def test_memory_only(db_client, bases):
    md, traj, sessions = bases
    paths = await _seed(db_client, md_base=md, traj_base=traj, sessions=sessions)

    result = await wipe_agent_data(
        db_client, TARGET, USER,
        clear_conversations=False, clear_memory=True,
        narrative_base=md, trajectory_base=traj, session_service=sessions,
    )

    # memory gone for target, intact for other
    for kind in MEMORY_KINDS:
        assert await _count(db_client, f"memory_{kind}", {"agent_id": TARGET}) == 0
        assert await _count(db_client, f"memory_{kind}", {"agent_id": OTHER}) == 1
    assert await _count(db_client, "instance_artifacts", {"agent_id": TARGET}) == 0
    assert await _count(db_client, "instance_artifacts", {"agent_id": OTHER}) == 1
    # conversation preserved
    assert await _count(db_client, "narratives", {"agent_id": TARGET}) == 2
    assert await _count(db_client, "events", {"agent_id": TARGET}) == 2
    assert os.path.exists(paths["nar_dir"])
    assert os.path.exists(paths["sess_file"])

    assert result.memory_rows_count >= len(MEMORY_KINDS)


@pytest.mark.asyncio
async def test_both_full_wipe_and_idempotent(db_client, bases):
    md, traj, sessions = bases
    paths = await _seed(db_client, md_base=md, traj_base=traj, sessions=sessions)

    await wipe_agent_data(
        db_client, TARGET, USER,
        clear_conversations=True, clear_memory=True,
        narrative_base=md, trajectory_base=traj, session_service=sessions,
    )

    # everything for target gone
    assert await _count(db_client, "narratives", {"agent_id": TARGET}) == 0
    for kind in MEMORY_KINDS:
        assert await _count(db_client, f"memory_{kind}", {"agent_id": TARGET}) == 0
    # identity preserved
    assert await _count(db_client, "agents", {"agent_id": TARGET}) == 1
    assert await _count(db_client, "module_instances", {"instance_id": "aware_1"}) == 1
    # other agent intact
    assert await _count(db_client, "narratives", {"agent_id": OTHER}) == 1
    for kind in MEMORY_KINDS:
        assert await _count(db_client, f"memory_{kind}", {"agent_id": OTHER}) == 1
    assert os.path.exists(paths["other_nar"])

    # idempotent — second run returns zeros, no exception
    again = await wipe_agent_data(
        db_client, TARGET, USER,
        clear_conversations=True, clear_memory=True,
        narrative_base=md, trajectory_base=traj, session_service=sessions,
    )
    assert again.narratives_count == 0
    assert again.memory_rows_count == 0
    assert again.disk_errors == []
