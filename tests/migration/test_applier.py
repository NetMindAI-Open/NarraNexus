"""
Migration applier — execute a plan onto a NarraNexus agent (create + populate).

Uses the shared db_client fixture (in-memory sqlite, migrated). Workspace base
is redirected to tmp so the skill file-copy is isolated. No network: only the
local-skill-copy path is exercised (marketplace fallback needs a live registry).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from xyz_agent_context.migration.applier import apply_plan
from xyz_agent_context.migration.mapper import build_plan
from xyz_agent_context.schema.migration_schema import (
    MigrationAgent,
    MigrationMcpServer,
    MigrationMemory,
    MigrationSession,
    MigrationSkill,
    MigrationSource,
    MigrationTurn,
    StandardizedAgentImport,
)
from xyz_agent_context.repository.agent_repository import AgentRepository
from xyz_agent_context.repository.instance_repository import InstanceRepository
from xyz_agent_context.repository.instance_awareness_repository import (
    InstanceAwarenessRepository,
)


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from xyz_agent_context.settings import settings
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))
    return tmp_path


def _import_with_local_skill(skill_dir: Path) -> StandardizedAgentImport:
    (skill_dir / "SKILL.md").write_text("# my skill", encoding="utf-8")
    return StandardizedAgentImport(
        source=MigrationSource(framework="claude_code", detected_path="/x", detection_confidence="high"),
        agent=MigrationAgent(name="Imported One", system_prompt="You are imported."),
        skills=[MigrationSkill(name="myskill", source="claude_code", local_path=str(skill_dir))],
        memory=[
            MigrationMemory(type="fact", content="user prefers Python", source_file="MEMORY.md"),
            MigrationMemory(type="fact", content="lives in Shanghai", source_file="MEMORY.md"),
        ],
        mcp_servers=[MigrationMcpServer(name="remote", transport="url", url="https://h/sse",
                                        headers={"Authorization": "Bearer x"})],
        sessions=[MigrationSession(
            session_id="s1", title="Plan the Q3 roadmap", started_at="2026-07-01T00:00:00Z",
            compact_text="Earlier: agreed on OKRs",
            turns=[MigrationTurn(role="user", text="what is next", ts="2026-07-01T00:00:00Z"),
                   MigrationTurn(role="assistant", text="ship the roadmap", ts="2026-07-01T00:00:05Z")],
        )],
    )


class _FakeHelperSDK:
    """Stub for get_helper_sdk() — returns a fixed structured summary so applier
    tests never hit a real LLM."""
    async def llm_function(self, instructions, user_input, output_type):
        class _R:
            final_output = output_type(
                description="A Q3 roadmap planning session",
                current_summary="Agreed OKRs; next is to ship the roadmap.",
                topic_hint="q3 roadmap", topic_keywords=["roadmap", "q3"],
                dynamic_summary=["Agreed on OKRs", "Decided to ship the roadmap"],
            )
        return _R()


@pytest.mark.asyncio
async def test_apply_creates_and_populates_agent(db_client, workspace, tmp_path, monkeypatch):
    skill_src = tmp_path / "src_skill"
    skill_src.mkdir()
    imp = _import_with_local_skill(skill_src)
    plan = build_plan(imp)

    # Default NarraNexus skills are provisioned for a new agent (same set a
    # normally-created agent gets). Stub the registry call to stay hermetic.
    async def _fake_defaults(self, aid, uid):
        return {"installed": ["netmind-vision", "officecli"], "skipped": [], "failed": []}
    import xyz_agent_context.marketplace.skill_marketplace_service as sms
    monkeypatch.setattr(sms.SkillMarketplaceService, "install_defaults", _fake_defaults)
    # Stub the session-summary helper LLM (one call per imported session).
    import xyz_agent_context.migration.applier as applier_mod
    monkeypatch.setattr(applier_mod, "get_helper_sdk", lambda: _FakeHelperSDK())
    # Capture the ChatModule instance-memory write (its table machinery uses
    # MySQL information_schema, which the direct sqlite test backend can't run —
    # the real write is ChatModule's own prod-proven path; here we assert the
    # message payload _seed_chat_history builds).
    seeded_calls: list = []
    import xyz_agent_context.repository.event_memory_repository as emr_mod
    async def _capture(self, module_name, instance_id, memory):
        seeded_calls.append((module_name, instance_id, memory))
        return True
    monkeypatch.setattr(emr_mod.EventMemoryRepository, "add_instance_json_format_memory", _capture)

    res = await apply_plan(db_client, user_id="user_x", plan=plan)

    # agent created + default skills installed
    assert res.created is True and res.agent_id.startswith("agent_")
    assert res.default_skills_installed == ["netmind-vision", "officecli"]
    agent = await AgentRepository(db_client).get_agent(res.agent_id)
    assert agent is not None and agent.agent_name == "Imported One"

    # awareness written to the AwarenessModule instance
    assert res.awareness_written is True
    insts = await InstanceRepository(db_client).get_by_agent(res.agent_id, module_class="AwarenessModule")
    aw = await InstanceAwarenessRepository(db_client).get_by_instance(insts[0].instance_id)
    assert "You are imported." in aw.awareness

    # memory: both facts retained
    assert res.memory_written == 2

    # skill copied verbatim into workspace/skills/<name>/
    assert res.skills_copied == ["myskill"]
    from xyz_agent_context.utils.workspace_paths import agent_workspace_path
    copied = agent_workspace_path(res.agent_id, "user_x") / "skills" / "myskill" / "SKILL.md"
    assert copied.exists()

    # url-mcp added
    assert res.mcp_added == ["remote"]

    # session → Narrative created (summarized), turns retained as scoped memory
    assert res.narratives_created == ["Plan the Q3 roadmap"]
    assert res.memory_turns_retained == 2
    from xyz_agent_context.narrative.narrative_service import NarrativeService
    narrs = await NarrativeService(res.agent_id, db_client).load_narratives_by_agent_user(
        res.agent_id, "user_x", 10)
    assert any(n.narrative_info.name == "Plan the Q3 roadmap" for n in narrs)
    n = next(n for n in narrs if n.narrative_info.name == "Plan the Q3 roadmap")
    assert "ship the roadmap" in n.narrative_info.current_summary   # enriched via helper_llm
    assert n.topic_keywords == ["roadmap", "q3"]
    # the imported turns are retained as EVENT memory scoped to the narrative
    # (event = append-only, not consolidated — observation would tombstone them)
    from xyz_agent_context.memory import MemoryEngine, SCOPE_NARRATIVE
    evs = await MemoryEngine(db_client, res.agent_id).recall(
        "event", "roadmap", scope_type=SCOPE_NARRATIVE, scope_id=n.id, limit=10)
    assert any("ship the roadmap" in o.content_text for o in evs)

    # AND the recent turns are ALSO seeded into the narrative's ChatModule
    # instance memory (so they load as recent history, not just search).
    assert seeded_calls, "recent turns seeded into ChatModule instance memory"
    mod, inst, mem = seeded_calls[0]
    assert mod == "ChatModule" and inst.startswith("chat_")       # the narrative's chat instance
    msgs = mem["messages"]
    assert [m["role"] for m in msgs] == ["user", "assistant"]     # order preserved
    assert any("ship the roadmap" in m["content"] for m in msgs)
    assert all(m["meta_data"]["source"] == "imported" for m in msgs)
    # THE core design decision: the sort key is the turn's ORIGINAL time, not
    # import time — pin it so a refactor can't silently switch to now().
    assert msgs[0]["meta_data"]["timestamp"] == "2026-07-01T00:00:00Z"
    assert msgs[1]["meta_data"]["timestamp"] == "2026-07-01T00:00:05Z"


@pytest.mark.asyncio
async def test_copy_local_skill_rejects_path_traversal(workspace, tmp_path):
    # A crafted skill name must not escape skills/ (it is rmtree'd + copytree'd,
    # so a `../..` name could delete/overwrite arbitrary dirs).
    from xyz_agent_context.migration.applier import _copy_local_skill
    src = tmp_path / "s"
    src.mkdir()
    (src / "SKILL.md").write_text("x", encoding="utf-8")
    for bad in ("../evil", "a/b", "..", "/abs"):
        assert await _copy_local_skill("agent_x", "user_x", bad, str(src)) is False
    # a plain name still works
    assert await _copy_local_skill("agent_x", "user_x", "good", str(src)) is True


@pytest.mark.asyncio
async def test_apply_no_local_source_marks_unmatched(db_client, workspace, monkeypatch):
    # a skill with no local_path and a marketplace that returns nothing → unmatched
    imp = StandardizedAgentImport(
        source=MigrationSource(framework="codex", detected_path="/x", detection_confidence="high"),
        agent=MigrationAgent(name="A", system_prompt="hi"),
        skills=[MigrationSkill(name="ghost-skill")],
    )
    plan = build_plan(imp)

    async def _empty_search(*a, **k):
        return {"items": []}
    async def _no_defaults(self, aid, uid):
        return {"installed": [], "skipped": [], "failed": []}
    import xyz_agent_context.marketplace.skill_marketplace_service as sms
    monkeypatch.setattr(sms.SkillMarketplaceService, "search", _empty_search)
    monkeypatch.setattr(sms.SkillMarketplaceService, "install_defaults", _no_defaults)

    res = await apply_plan(db_client, user_id="user_x", plan=plan)
    assert res.skills_unmatched == ["ghost-skill"]
    assert res.skills_copied == [] and res.skills_installed == []
