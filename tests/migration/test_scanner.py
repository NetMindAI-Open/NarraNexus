"""
Agent Migration Scanner — detection + extraction against the REAL per-framework
source layouts (verified vs Hermes agent_import.py / openclaw_to_hermes.py).

Hermetic: Path.home() is monkeypatched to tmp so no real ~/.claude leaks in.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from xyz_agent_context.migration import detector, scanner
from xyz_agent_context.migration.extractors import _memory_from_md, _mcp_from_dict, _encode_cwd


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


# ── fixtures per real layout ────────────────────────────────────────────────

def _mk_claude_global(home: Path):
    # global config lives in ~/.claude.json (NEXT TO ~/.claude/)
    (home / ".claude.json").write_text(json.dumps({
        "mcpServers": {
            "filesystem": {"command": "npx", "args": ["-y", "@mcp/fs"], "env": {"TOKEN": "abc"}},
            "remote": {"url": "https://mcp.example.com/sse", "headers": {"Authorization": "Bearer x"}},
        },
        "projects": {str(home / "proj"): {"mcpServers": {}}},
    }), encoding="utf-8")
    cd = home / ".claude"
    (cd / "skills" / "web-search").mkdir(parents=True)
    (cd / "settings.json").write_text("{}", encoding="utf-8")
    (cd / "CLAUDE.md").write_text("Global: always be concise.", encoding="utf-8")
    return cd


def _mk_claude_project(home: Path):
    proj = home / "proj"
    proj.mkdir()
    (proj / "CLAUDE.md").write_text("You are a project Claude agent.\n- prefers Python", encoding="utf-8")
    (proj / ".env").write_text("OPENAI_API_KEY=sk-secret\n", encoding="utf-8")
    # a realistic session transcript under ~/.claude/projects/<encoded cwd>/
    sess = home / ".claude" / "projects" / _encode_cwd(proj)
    sess.mkdir(parents=True)
    lines = [
        {"type": "ai-title", "aiTitle": "Refactor the auth flow"},
        {"type": "user", "message": {"role": "user", "content": "help me refactor auth"},
         "timestamp": "2026-07-01T00:00:00Z"},
        {"type": "assistant", "message": {"role": "assistant", "content": [
            {"type": "thinking", "text": "hmm"},
            {"type": "text", "text": "Sure, here is the plan"},
            {"type": "tool_use", "name": "edit"}]}},
        # tool_result on a user line — must be filtered out (not a real turn)
        {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "content": "file written"}]}},
        # a sidechain turn — must be filtered out
        {"type": "assistant", "isSidechain": True,
         "message": {"role": "assistant", "content": [{"type": "text", "text": "subagent noise"}]}},
        # the source's own compact rollup — must be kept
        {"type": "user", "isCompactSummary": True,
         "message": {"role": "user", "content": "Summary of earlier work: set up JWT"}},
    ]
    (sess / "s1.jsonl").write_text(
        "\n".join(json.dumps(x) for x in lines) + "\n", encoding="utf-8")
    return proj


def _mk_codex(home: Path):
    d = home / ".codex"
    (d / "skills" / "coder").mkdir(parents=True)
    (d / "memories").mkdir(parents=True)
    (d / "AGENTS.md").write_text("Codex agent instructions.", encoding="utf-8")
    (d / "config.toml").write_text(
        'model = "gpt"\n[mcp_servers.fs]\ncommand = "npx"\nargs = ["-y", "srv"]\n', encoding="utf-8")
    (d / "memories" / "notes.md").write_text("- remembers X", encoding="utf-8")
    return d


def _mk_openclaw(home: Path):
    d = home / ".openclaw"
    (d / "workspace").mkdir(parents=True)
    (d / "skills" / "s").mkdir(parents=True)
    (d / "openclaw.json").write_text("{}", encoding="utf-8")
    (d / "workspace" / "SOUL.md").write_text("I am OpenClaw.", encoding="utf-8")
    (d / "workspace" / "MEMORY.md").write_text("- lives in Beijing", encoding="utf-8")
    return d


# ── detection ────────────────────────────────────────────────────────────────

def test_detect_all(home):
    _mk_claude_global(home)
    _mk_codex(home)
    _mk_openclaw(home)
    found = {d.framework for d in detector.detect_all(home)}
    assert {"claude_code", "codex", "openclaw"} <= found


def test_detect_enumerates_claude_projects(home):
    # A project with CLAUDE.md + a session must surface as its OWN detection
    # (one project = one importable agent), ranked above the global fallback.
    _mk_claude_global(home)
    proj = _mk_claude_project(home)  # ~/.claude.json already lists it
    cc = [d for d in detector.detect_all(home) if d.framework == "claude_code"]
    # the project (high, has CLAUDE.md) + the global fallback (low)
    proj_dets = [d for d in cc if d.path == str(proj)]
    assert len(proj_dets) == 1
    assert proj_dets[0].confidence == "high"
    assert "has:CLAUDE.md" in proj_dets[0].signals
    assert any(s.startswith("sessions:") for s in proj_dets[0].signals)
    # global entry is present but demoted to a fallback
    globals_ = [d for d in cc if "global-shared-config" in d.signals]
    assert len(globals_) == 1 and globals_[0].confidence == "low"
    # the project outranks the global fallback in the returned order
    assert cc.index(proj_dets[0]) < cc.index(globals_[0])


def test_detect_skips_empty_projects(home):
    # A project listed in ~/.claude.json but with NO CLAUDE.md and NO sessions is
    # noise (a dir opened once) — it must NOT be enumerated; only the global
    # fallback remains for claude_code.
    import json as _json
    empty = home / "opened-once"
    empty.mkdir()
    (home / ".claude.json").write_text(_json.dumps({
        "mcpServers": {}, "projects": {str(empty): {}},
    }), encoding="utf-8")
    (home / ".claude" / "settings.json").parent.mkdir(parents=True, exist_ok=True)
    (home / ".claude" / "settings.json").write_text("{}", encoding="utf-8")
    cc = [d for d in detector.detect_all(home) if d.framework == "claude_code"]
    assert not any(d.path == str(empty) for d in cc)              # empty project dropped
    assert all("global-shared-config" in d.signals for d in cc)  # only the fallback left


# ── claude: global vs project ────────────────────────────────────────────────

def test_scan_claude_global(home):
    cd = _mk_claude_global(home)
    r = scanner.scan(path=cd)
    assert r.source.framework == "claude_code"
    # global: skills + mcp from ~/.claude.json (stdio env + url headers carried)
    assert {s.name for s in r.skills} == {"web-search"}
    by = {m.name: m for m in r.mcp_servers}
    assert by["filesystem"].env == {"TOKEN": "abc"}
    assert by["remote"].transport == "url"


def test_scan_claude_project_with_sessions(home):
    _mk_claude_global(home)
    proj = _mk_claude_project(home)
    r = scanner.scan(path=proj, framework="claude_code")
    # CLAUDE.md → Awareness, combined global + project (both present, labelled)
    assert "project Claude agent" in r.agent.system_prompt
    assert "User-level instructions" in r.agent.system_prompt      # global section header
    # sessions = ours: one session parsed from the .jsonl
    assert len(r.sessions) == 1
    s = r.sessions[0]
    assert s.title == "Refactor the auth flow"                     # ai-title → name
    assert "set up JWT" in s.compact_text                          # isCompactSummary kept
    texts = [t.text for t in s.turns]
    assert "help me refactor auth" in texts                        # real user turn
    assert "Sure, here is the plan" in texts                       # assistant text block
    assert not any("file written" in t for t in texts)             # tool_result filtered
    assert not any("subagent noise" in t for t in texts)           # sidechain filtered
    assert "OPENAI_API_KEY" in r.custom.credential_keys            # keys only, no value
    assert not any("sk-secret" in c for c in r.custom.credential_keys)


def test_extract_exception_degrades_to_empty_sessions(home, monkeypatch):
    # If extraction blows up mid-scan, extract() must degrade to an EMPTY
    # StandardizedAgentImport (sessions=[]), not sessions="" — the latter fails
    # pydantic validation and turns a recoverable parse error into a 400.
    from xyz_agent_context.migration import extractors
    def _boom(_base):
        raise RuntimeError("kaboom")
    monkeypatch.setattr(extractors, "_extract_claude_code", _boom)
    agent, skills, memory, mcp, custom, sessions = extractors.extract("claude_code", "/x")
    assert sessions == []                          # the bug: was ""
    assert "kaboom" in custom.llm_fallback_notes
    # and it packs into a valid schema without raising
    _mk_claude_global(home)
    r = scanner.scan(path=home / ".claude", framework="claude_code")
    assert r.sessions == []


def test_encode_cwd_replaces_all_non_alnum():
    # Claude Code encodes the cwd by replacing EVERY non-alphanumeric char with
    # '-', not just '/'. A '/'-only replace silently misses '_' and '.' and finds
    # zero sessions (real-data regression, 2026-07-30).
    from pathlib import Path
    assert _encode_cwd(Path("/Users/x/xyz_proto_test/App.v2")) == "-Users-x-xyz-proto-test-App-v2"


def test_scan_claude_skills_dedup_project_wins(home):
    # global has web-search; project has web-search (same name) + pdf.
    _mk_claude_global(home)                       # global skill: web-search
    proj = _mk_claude_project(home)
    (proj / ".claude" / "skills" / "web-search").mkdir(parents=True)
    (proj / ".claude" / "skills" / "pdf").mkdir(parents=True)
    r = scanner.scan(path=proj, framework="claude_code")
    names = [s.name for s in r.skills]
    assert names.count("web-search") == 1                         # deduped, not twice
    web = next(s for s in r.skills if s.name == "web-search")
    assert web.scope == "project"                                 # project wins the clash
    assert web.local_path.endswith("/proj/.claude/skills/web-search")
    assert {s.name for s in r.skills} == {"web-search", "pdf"}


# ── codex: config.toml mcp + memories ────────────────────────────────────────

def test_scan_codex(home):
    d = _mk_codex(home)
    r = scanner.scan(path=d, framework="codex")
    assert "Codex agent instructions" in r.agent.system_prompt
    assert any(m.name == "fs" and m.command == "npx" for m in r.mcp_servers)  # config.toml
    assert any("remembers X" in m.content for m in r.memory)                   # memories/*.md
    assert {s.name for s in r.skills} == {"coder"}


# ── openclaw: persona/memory under workspace/ ────────────────────────────────

def test_scan_openclaw_workspace(home):
    d = _mk_openclaw(home)
    r = scanner.scan(path=d, framework="openclaw")
    assert r.agent.system_prompt == "I am OpenClaw."             # workspace/SOUL.md
    assert any("lives in Beijing" in m.content for m in r.memory)  # workspace/MEMORY.md


# ── helpers ──────────────────────────────────────────────────────────────────

def test_memory_from_md_bullets_vs_prose():
    facts = _memory_from_md("- a\n- b", "MEMORY.md")
    assert [m.content for m in facts] == ["a", "b"]
    assert _memory_from_md("just prose", "MEMORY.md")[0].type == "note"


def test_mcp_from_dict_shapes():
    m = _mcp_from_dict({
        "a": {"command": "x", "args": ["y"], "env": {"K": "v"}},
        "b": {"url": "https://h", "headers": {"H": "1"}},
        "junk": "not-a-dict",
    })
    by = {s.name: s for s in m}
    assert by["a"].transport == "stdio" and by["a"].env == {"K": "v"}
    assert by["b"].transport == "url"
    assert "junk" not in by
