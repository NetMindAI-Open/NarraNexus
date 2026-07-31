"""
@file_name: extractors.py
@author: NetMind.AI
@date: 2026-07-21
@description: Per-framework extraction into the standardized migration JSON.

Source layouts verified against Hermes `agent_import.py` /
`openclaw_to_hermes.py` (Nous Research, MIT) — the detect+extract rules are
mirrored (reimplemented in our style + conventions, with attribution). The
map+write half and the session→Narrative step are ours.

Real per-framework layouts:
- Claude Code: MCP in ``~/.claude.json`` mcpServers (+ settings.json); agent
  instructions in ``<cwd>/CLAUDE.md``; global skills in ``~/.claude/skills/``;
  SESSIONS in ``~/.claude/projects/<encoded-cwd>/*.jsonl`` (ours to use).
- Codex: ``~/.codex/AGENTS.md`` + ``config.toml`` mcp_servers + ``memories/*.md``
  + ``skills/``.
- OpenClaw: persona/memory under ``~/.openclaw/workspace/{SOUL,MEMORY,USER}.md``
  + ``workspace/memory/``; ``skills/`` (+ shared); ``openclaw.json`` mcp.
- Hermes: ``~/.hermes/{SOUL,MEMORY,USER}.md``, config.yaml, skills/.

Best-effort: a missing/malformed file degrades to empty, never raises.
Credential policy (Owner): MCP env/header VALUES are carried; other secrets
contribute KEY NAMES only to custom.credential_keys.
"""

from __future__ import annotations

import json
import re
from collections import deque
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from loguru import logger

from xyz_agent_context.schema.migration_schema import (
    AWARENESS_IMPORT_CHAR_LIMIT,
    Framework,
    MigrationAgent,
    MigrationCustom,
    MigrationMcpServer,
    MigrationMemory,
    MigrationSession,
    MigrationSkill,
    MigrationTurn,
)

# Cap a single memory entry so a huge doc doesn't dominate (mirrors Hermes).
_MEMORY_ENTRY_CHAR_LIMIT = 20_000
# Session parsing bounds (a single Claude session .jsonl can be 100MB+).
_SESSION_MAX_FILES = 20          # most-recent session files parsed per project
_SESSION_RECENT_TURNS = 200      # rolling window of recent turns held per session
_SESSION_TURN_CHAR_BUDGET = 16_000   # of those, keep the newest up to this many chars
_SESSION_COMPACT_CHAR_BUDGET = 16_000  # cap on the source's own compact rollups


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:  # noqa: BLE001
        return ""


def _first_existing(base: Path, names: List[str]) -> Tuple[str, str]:
    for n in names:
        p = base / n
        if p.exists() and p.is_file():
            return _read(p), n
    return "", ""


def _memory_from_md(text: str, source_file: str) -> List[MigrationMemory]:
    """Bullet lines → one fact each; otherwise the whole doc → one note."""
    if not text.strip():
        return []
    bullets = [
        re.sub(r"^\s*[-*]\s+", "", ln).strip()
        for ln in text.splitlines()
        if re.match(r"^\s*[-*]\s+\S", ln)
    ]
    if bullets:
        return [MigrationMemory(type="fact", content=b[:_MEMORY_ENTRY_CHAR_LIMIT],
                                source_file=source_file) for b in bullets]
    return [MigrationMemory(type="note", content=text.strip()[:_MEMORY_ENTRY_CHAR_LIMIT],
                            source_file=source_file)]


def _memory_from_dir(mem_dir: Path) -> List[MigrationMemory]:
    """Each *.md under a memory/ dir contributes memory entries."""
    out: List[MigrationMemory] = []
    if mem_dir.exists() and mem_dir.is_dir():
        for f in sorted(mem_dir.glob("*.md")):
            out += _memory_from_md(_read(f), f"{mem_dir.name}/{f.name}")
    return out


# Secret-bearing patterns for MCP fields. MCP creds hide in args/url too, not
# just env/headers (e.g. `--api-key=...`, `?token=`, `sk-...`). We flag which
# fields carry a secret so the plaintext preview can highlight + warn.
_SECRET_RE = re.compile(
    r"(api[_-]?key|secret|token|password|passwd|bearer|authorization|access[_-]?key"
    r"|sk-[A-Za-z0-9]|v2\.[A-Za-z0-9_]{20})",
    re.IGNORECASE,
)


def _looks_secret(text: str) -> bool:
    return bool(text) and bool(_SECRET_RE.search(text))


def _flag_secret_fields(srv: MigrationMcpServer) -> None:
    """Populate srv.secret_fields with the dotted paths that carry a secret."""
    fields: List[str] = []
    # _looks_secret already scans the whole URL (query included), so no separate
    # query check is needed.
    if srv.url and _looks_secret(srv.url):
        fields.append("url")
    for i, a in enumerate(srv.args):
        if _looks_secret(a):
            fields.append(f"args[{i}]")
    for k, v in srv.env.items():
        if _looks_secret(k) or _looks_secret(v):
            fields.append(f"env.{k}")
    for k, v in srv.headers.items():
        if _looks_secret(k) or _looks_secret(v):
            fields.append(f"headers.{k}")
    srv.secret_fields = fields


def _mcp_from_dict(servers: Dict) -> List[MigrationMcpServer]:
    """A ``name -> cfg`` map (Claude .claude.json / .mcp.json, Codex config.toml
    mcp_servers, ...) → MigrationMcpServer list. stdio + url shapes."""
    out: List[MigrationMcpServer] = []
    if not isinstance(servers, dict):
        return out
    for name, cfg in servers.items():
        if not isinstance(cfg, dict):
            continue
        if cfg.get("url"):
            out.append(MigrationMcpServer(
                name=str(name), transport="url", url=cfg.get("url"),
                headers={str(k): str(v) for k, v in (cfg.get("headers") or {}).items()},
            ))
        elif cfg.get("command"):
            out.append(MigrationMcpServer(
                name=str(name), transport="stdio", command=str(cfg["command"]),
                args=[str(a) for a in (cfg.get("args") or [])],
                env={str(k): str(v) for k, v in (cfg.get("env") or {}).items()},
            ))
    for srv in out:
        _flag_secret_fields(srv)
    return out


def _load_json(path: Path) -> Dict:
    try:
        return json.loads(_read(path)) or {}
    except Exception:  # noqa: BLE001
        return {}


def _skills_from_dir(skills_dir: Path, source: str) -> List[MigrationSkill]:
    """Each immediate subdirectory of a skills/ folder is one skill."""
    out: List[MigrationSkill] = []
    if skills_dir.exists() and skills_dir.is_dir():
        for child in sorted(skills_dir.iterdir()):
            if child.is_dir():
                out.append(MigrationSkill(name=child.name, source=source, local_path=str(child)))
    return out


def _env_keys(base: Path) -> List[str]:
    """KEY names from a .env (values NEVER read)."""
    keys = []
    for ln in _read(base / ".env").splitlines():
        m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=", ln)
        if m:
            keys.append(m.group(1))
    return keys


def _encode_cwd(cwd: Path) -> str:
    """Claude Code encodes a project's cwd into its projects/ dir name by
    replacing EVERY non-alphanumeric char (``/``, ``_``, ``.``, ...) with '-'
    (e.g. /Users/x/xyz_proto_test/App.v2 → -Users-x-xyz-proto-test-App-v2).
    Verified against a real ~/.claude/projects layout — a '/'-only replace
    silently misses '_' and '.' and finds zero sessions."""
    return re.sub(r"[^a-zA-Z0-9]", "-", str(cwd))


def _combine_claude_md(claude_home: Path, cwd: Path) -> str:
    """Awareness = the source's EFFECTIVE instructions = global (~/.claude/CLAUDE.md,
    user-level) + project (<cwd>/CLAUDE.md) + local (<cwd>/CLAUDE.local.md),
    section-labelled and length-capped. Claude Code layers all three at runtime;
    importing only one loses instructions."""
    sections: List[Tuple[str, Path]] = [
        ("## User-level instructions (~/.claude/CLAUDE.md)", claude_home / "CLAUDE.md"),
        ("## Project instructions (CLAUDE.md)", cwd / "CLAUDE.md"),
        ("## Local overrides (CLAUDE.local.md)", cwd / "CLAUDE.local.md"),
    ]
    parts = [f"{h}\n\n{txt}" for h, p in sections if (txt := _read(p).strip())]
    return "\n\n".join(parts)[:AWARENESS_IMPORT_CHAR_LIMIT]


def _claude_skills(cwd: Path, claude_home: Path) -> List[MigrationSkill]:
    """Project skills (<cwd>/.claude/skills) + global skills (~/.claude/skills),
    deduped by name with the PROJECT skill winning a same-name clash (it is the
    more specific one). Each tagged with its scope for the preview."""
    proj = _skills_from_dir(cwd / ".claude" / "skills", "claude_code_project")
    for s in proj:
        s.scope = "project"
    glob = _skills_from_dir(claude_home / "skills", "claude_code_global")
    for s in glob:
        s.scope = "global"
    seen = {s.name for s in proj}
    return proj + [s for s in glob if s.name not in seen]


def _iter_jsonl(path: Path):
    """Stream a (possibly huge) .jsonl, yielding parsed objects; bad lines skip."""
    try:
        with path.open(encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if not ln:
                    continue
                try:
                    yield json.loads(ln)
                except Exception:  # noqa: BLE001
                    continue
    except Exception:  # noqa: BLE001
        return


def _message_text(content) -> str:
    """Real prose from a Claude message.content: a str, or the `text` blocks of a
    list (dropping tool_use / tool_result / thinking / image blocks)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        return " ".join(
            b["text"] for b in content
            if isinstance(b, dict) and b.get("type") == "text" and b.get("text")
        ).strip()
    return ""


def _parse_claude_session_file(path: Path) -> Optional[MigrationSession]:
    """Parse ONE Claude session .jsonl → a MigrationSession. Keeps: the ai-title,
    the source's own isCompactSummary rollups, and the recent real user/assistant
    turns. Drops tool_result / thinking / tool_use / sidechain / meta noise."""
    title = ""
    started_at = ""
    compacts: deque = deque(maxlen=10)
    recent: deque = deque(maxlen=_SESSION_RECENT_TURNS)  # (role, text, ts), chronological

    for o in _iter_jsonl(path):
        if not isinstance(o, dict):
            continue
        t = o.get("type")
        if t == "ai-title":
            at = o.get("aiTitle")
            if isinstance(at, str) and at.strip():
                title = at.strip()  # rolling — keep the latest
            continue
        if t not in ("user", "assistant"):
            continue
        ts = o.get("timestamp") or ""
        if not started_at and ts:
            started_at = ts
        content = (o.get("message") or {}).get("content") if isinstance(o.get("message"), dict) else None
        # Compact rollups are the source's own history summary — keep even though
        # they ride on a user line.
        if o.get("isCompactSummary"):
            txt = _message_text(content)
            if txt:
                compacts.append(txt)
            continue
        if o.get("isSidechain") or o.get("isMeta") or o.get("isVisibleInTranscriptOnly"):
            continue
        txt = _message_text(content)
        if txt:
            recent.append((t, txt, ts))

    # Keep the newest turns within the char budget, restored to chronological order.
    turns_rev: List[MigrationTurn] = []
    used = 0
    for role, txt, ts in reversed(recent):
        if used + len(txt) > _SESSION_TURN_CHAR_BUDGET and turns_rev:
            break
        turns_rev.append(MigrationTurn(role=role, text=txt, ts=ts))
        used += len(txt)
    turns = list(reversed(turns_rev))

    compact_text = "\n\n".join(compacts)[-_SESSION_COMPACT_CHAR_BUDGET:]
    if not turns and not compact_text:
        return None
    return MigrationSession(
        session_id=path.stem, title=title, compact_text=compact_text,
        turns=turns, started_at=started_at,
    )


def _claude_sessions(claude_home: Path, cwd: Path) -> List[MigrationSession]:
    """All (recent) sessions of a project → MigrationSession list, newest first.
    One .jsonl under ~/.claude/projects/<encoded-cwd>/ = one session = one
    Narrative downstream. Hermes ignores sessions — this is ours."""
    sess_dir = claude_home / "projects" / _encode_cwd(cwd)
    if not sess_dir.exists():
        return []
    files = sorted(sess_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    out: List[MigrationSession] = []
    for f in files[:_SESSION_MAX_FILES]:
        s = _parse_claude_session_file(f)
        if s is not None:
            out.append(s)
    return out


# ── per-framework extractors ────────────────────────────────────────────────
# Each returns (agent, skills, memory, mcp_servers, custom, sessions).

def _extract_claude_code(base: Path):
    """Two shapes:
    - base == ~/.claude (global): global skills + global mcp (~/.claude.json).
    - base == a project cwd: CLAUDE.md → system_prompt; project + global mcp;
      project skills; session seed from ~/.claude/projects/<encoded cwd>.
    """
    home = Path.home()
    claude_home = home / ".claude"
    claude_json = _load_json(home / ".claude.json")
    global_mcp = _mcp_from_dict(claude_json.get("mcpServers") or {})
    # also settings.json mcpServers (secondary)
    global_mcp += _mcp_from_dict(_load_json(claude_home / "settings.json").get("mcpServers") or {})

    is_global = base.resolve() == claude_home.resolve()
    if is_global:
        agent = MigrationAgent(
            name="Claude Code Agent",
            system_prompt=_read(base / "CLAUDE.md").strip()[:AWARENESS_IMPORT_CHAR_LIMIT],
        )
        skills = _skills_from_dir(base / "skills", "claude_code")
        for s in skills:
            s.scope = "global"
        # list projects as candidates (info) so the user can rescan a project cwd
        projects = list((claude_json.get("projects") or {}).keys())
        custom = MigrationCustom(
            credential_keys=_env_keys(base),
            llm_fallback_notes=(
                f"Global Claude config. {len(projects)} project(s) available — "
                f"rescan a specific project cwd to import its CLAUDE.md + sessions: "
                + ", ".join(projects[:10])
            ) if projects else "",
        )
        return agent, skills, [], global_mcp, custom, []

    # project cwd: awareness = global+project+local CLAUDE.md; project+global skills
    # (project wins); all recent sessions → one Narrative each downstream.
    agent = MigrationAgent(
        name=base.name or "Claude Code Agent",
        system_prompt=_combine_claude_md(claude_home, base),
    )
    mcp = list(global_mcp)
    mcp += _mcp_from_dict(_load_json(base / ".mcp.json").get("mcpServers") or {})
    proj_cfg = (claude_json.get("projects") or {}).get(str(base.resolve()), {})
    mcp += _mcp_from_dict(proj_cfg.get("mcpServers") or {})
    skills = _claude_skills(base, claude_home)
    sessions = _claude_sessions(claude_home, base)
    custom = MigrationCustom(credential_keys=_env_keys(base))
    return agent, skills, [], mcp, custom, sessions


def _extract_soul_based(base: Path, fw: Framework):
    """Hermes / OpenClaw. OpenClaw persona/memory live under workspace/;
    Hermes at the root. Try both."""
    ws_candidates = [base / "workspace", base / "workspace.default", base]
    ws = next((w for w in ws_candidates if (w / "SOUL.md").exists()), base)
    sys_prompt, _ = _first_existing(ws, ["SOUL.md", "AGENTS.md"])
    agent = MigrationAgent(name=(base.name.lstrip(".") or fw), system_prompt=sys_prompt)
    mem_text, mem_file = _first_existing(ws, ["MEMORY.md"])
    memory = _memory_from_md(mem_text, mem_file)
    memory += _memory_from_dir(ws / "memory")
    user_text, user_file = _first_existing(ws, ["USER.md"])
    if user_text.strip():
        memory.append(MigrationMemory(type="profile", content=user_text.strip()[:_MEMORY_ENTRY_CHAR_LIMIT],
                                      source_file=user_file))
    skills = _skills_from_dir(base / "skills", fw)
    mcp: List[MigrationMcpServer] = []
    for cfg_name in ("openclaw.json", "clawdbot.json", "moltbot.json"):
        cfg = _load_json(base / cfg_name)
        mcp += _mcp_from_dict(cfg.get("mcpServers") or cfg.get("mcp_servers") or {})
    custom = MigrationCustom(credential_keys=_env_keys(base))
    return agent, skills, memory, mcp, custom, []


def _extract_codex(base: Path):
    sys_prompt, _ = _first_existing(base, ["AGENTS.md", "instructions.md"])
    agent = MigrationAgent(name="Codex Agent", system_prompt=sys_prompt)
    # config.toml → mcp_servers (TOML)
    mcp: List[MigrationMcpServer] = []
    toml_path = base / "config.toml"
    if toml_path.exists():
        try:
            import tomllib
            data = tomllib.loads(_read(toml_path))
            mcp = _mcp_from_dict(data.get("mcp_servers") or {})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"codex config.toml parse failed: {e}")
    memory = _memory_from_dir(base / "memories")
    skills = _skills_from_dir(base / "skills", "codex")
    custom = MigrationCustom(credential_keys=_env_keys(base))
    return agent, skills, memory, mcp, custom, []


def _extract_custom(base: Path):
    sys_prompt, _ = _first_existing(base, ["AGENTS.md", "CLAUDE.md", "SOUL.md"])
    agent = MigrationAgent(name=base.name.lstrip(".") or "imported-agent", system_prompt=sys_prompt)
    unmapped = [p.name for p in base.iterdir() if p.is_file()] if base.exists() else []
    custom = MigrationCustom(
        unmapped_files=unmapped,
        credential_keys=_env_keys(base),
        llm_fallback_notes="Custom framework — LLM fallback mapping recommended.",
    )
    return agent, [], [], [], custom, []


def extract(framework: Framework, path: str | Path):
    """Dispatch. Returns (agent, skills, memory, mcp_servers, custom, sessions)."""
    base = Path(path).expanduser()
    try:
        if framework == "claude_code":
            return _extract_claude_code(base)
        if framework in ("hermes", "openclaw"):
            return _extract_soul_based(base, framework)
        if framework == "codex":
            return _extract_codex(base)
        return _extract_custom(base)
    except Exception as e:  # noqa: BLE001 — extraction must never crash a scan
        logger.warning(f"migration.extract({framework}, {base}) failed: {e}")
        return MigrationAgent(), [], [], [], MigrationCustom(llm_fallback_notes=str(e)), []
