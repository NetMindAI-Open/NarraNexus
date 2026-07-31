"""
Migration mapper — standardized JSON → concrete write plan (the convert step).
"""
from __future__ import annotations

from xyz_agent_context.migration.mapper import build_plan
from xyz_agent_context.schema.migration_schema import (
    MigrationAgent,
    MigrationCustom,
    MigrationMcpServer,
    MigrationMemory,
    MigrationSession,
    MigrationSkill,
    MigrationSource,
    MigrationTurn,
    StandardizedAgentImport,
)


def _imp(**over) -> StandardizedAgentImport:
    base = dict(
        source=MigrationSource(framework="claude_code", detected_path="/x", detection_confidence="high"),
        agent=MigrationAgent(name="Neo", system_prompt="You are Neo."),
        skills=[MigrationSkill(name="web-search", local_path="/x/web-search"), MigrationSkill(name="pdf")],
        memory=[MigrationMemory(type="fact", content="user likes tea", source_file="MEMORY.md")],
        mcp_servers=[
            MigrationMcpServer(name="remote", transport="url", url="https://h/sse",
                               headers={"Authorization": "Bearer x"}, secret_fields=["headers.Authorization"]),
            MigrationMcpServer(name="fs", transport="stdio", command="npx", args=["-y", "srv"]),
        ],
        custom=MigrationCustom(credential_keys=["OPENAI_API_KEY"]),
    )
    base.update(over)
    return StandardizedAgentImport(**base)


def test_plan_core_mapping():
    p = build_plan(_imp())
    assert p.agent_name == "Neo"
    assert p.awareness_markdown == "You are Neo."          # system_prompt → Awareness
    assert [s.name for s in p.skills] == ["web-search", "pdf"]
    assert p.skills[0].local_path == "/x/web-search"       # carried for faithful copy
    assert [m.content for m in p.memory] == ["user likes tea"]


def test_plan_splits_mcp_url_vs_stdio():
    p = build_plan(_imp())
    assert [s.name for s in p.mcp_url_servers] == ["remote"]   # importable now
    assert [s.name for s in p.mcp_stdio_servers] == ["fs"]     # deferred
    # stdio not-imported warning + secret warning both present
    assert any("stdio MCP" in w for w in p.warnings)
    assert any("secrets" in w and "remote" in w for w in p.warnings)


def test_plan_narratives_from_sessions():
    imp = _imp(sessions=[
        MigrationSession(
            session_id="s1", title="Refactor auth", started_at="2026-07-01T00:00:00Z",
            compact_text="Earlier: set up JWT",
            turns=[MigrationTurn(role="user", text="help me"),
                   MigrationTurn(role="assistant", text="sure")],
        ),
    ])
    p = build_plan(imp)
    assert len(p.narratives) == 1
    n = p.narratives[0]
    assert n.title == "Refactor auth"                       # ai-title → name (no LLM)
    assert n.session_id == "s1"
    assert "set up JWT" in n.summary_source                 # compact first
    assert "User: help me" in n.summary_source              # then rendered turns
    assert [t.role for t in n.turns] == ["user", "assistant"]  # turns carried for memory


def test_plan_skill_scope_carried():
    p = build_plan(_imp())
    web = next(s for s in p.skills if s.name == "web-search")
    assert web.scope in ("project", "global", "")           # scope threaded through


def test_plan_credential_and_custom_warnings():
    p = build_plan(_imp(
        source=MigrationSource(framework="custom", detected_path="/x", detection_confidence="low"),
    ))
    assert any("credential key" in w for w in p.warnings)
    assert any("Custom/unknown framework" in w for w in p.warnings)
