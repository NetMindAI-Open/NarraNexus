"""
@file_name: detector.py
@author: NetMind.AI
@date: 2026-07-21
@description: Framework detection for Agent Migration.

Signal-driven detection of Claude Code / Hermes / OpenClaw / Codex agent
configs on the local filesystem. Detect-only — never reads secrets, never
writes. Mirrors the source-dir conventions Hermes `import-agent` targets.

Two entry points:
- ``detect_all(home)``   — probe the standard home locations, return every
  framework found (multi-framework coexistence, PRD P1).
- ``classify_path(path)``— classify one explicit directory the user pointed at.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from xyz_agent_context.schema.migration_schema import (
    Confidence,
    Framework,
    FrameworkDetection,
)

_ORDER = {"high": 3, "medium": 2, "low": 1}

# Per-framework signal table. ``home_dirs`` are the standard locations probed by
# detect_all (relative to the user's home). ``strong`` files, when present in the
# scanned dir, give high confidence; ``weak`` files alone give medium.
_SIGNALS: dict[Framework, dict] = {
    "claude_code": {
        "home_dirs": [".claude"],
        "strong": [".mcp.json", "settings.json", "CLAUDE.md"],
        "weak": ["commands", "skills", "projects", "CLAUDE.local.md"],
    },
    "hermes": {
        "home_dirs": [".hermes"],
        "strong": ["config.yaml", "SOUL.md"],
        "weak": ["MEMORY.md", "USER.md", "AGENTS.md", "skills"],
    },
    "openclaw": {
        # legacy dir names Hermes also recognises
        "home_dirs": [".openclaw", ".clawdbot", ".moltbot"],
        "strong": ["openclaw.json", "clawdbot.json", "moltbot.json", "SOUL.md"],
        "weak": ["MEMORY.md", "USER.md", "skills"],
    },
    "codex": {
        "home_dirs": [".codex"],
        "strong": ["config.toml", "config.json", "AGENTS.md"],
        "weak": ["instructions.md", "prompts"],
    },
}


def _score_dir(path: Path, framework: Framework) -> Optional[FrameworkDetection]:
    """Score one directory against one framework's signals. None = no match."""
    if not path.exists() or not path.is_dir():
        return None
    sig = _SIGNALS[framework]
    strong_hits = [f for f in sig["strong"] if (path / f).exists()]
    weak_hits = [f for f in sig["weak"] if (path / f).exists()]
    # The dir itself being a known home dir is a signal too.
    dir_is_home = path.name in sig["home_dirs"]

    if not strong_hits and not weak_hits and not dir_is_home:
        return None

    confidence: Confidence
    if strong_hits and (dir_is_home or len(strong_hits) >= 2):
        confidence = "high"
    elif strong_hits or dir_is_home:
        confidence = "medium"
    else:
        confidence = "low"

    signals = []
    if dir_is_home:
        signals.append(f"dir={path.name}")
    signals += [f"has:{f}" for f in strong_hits + weak_hits]
    return FrameworkDetection(
        framework=framework, path=str(path), confidence=confidence, signals=signals
    )


def classify_path(path: str | Path) -> FrameworkDetection:
    """Classify one explicit directory. Falls back to 'custom' (low) if nothing
    matches — the Custom-Importer heuristic + LLM fallback handles that case."""
    p = Path(path).expanduser()
    best: Optional[FrameworkDetection] = None
    order = {"high": 3, "medium": 2, "low": 1}
    for fw in _SIGNALS:
        d = _score_dir(p, fw)
        if d and (best is None or order[d.confidence] > order[best.confidence]):
            best = d
    if best is not None:
        return best
    # Custom fallback: any of the generic instruction/config hints?
    hints = [f for f in ("AGENTS.md", "CLAUDE.md", "config.yaml", "config.json")
             if (p / f).exists()]
    return FrameworkDetection(
        framework="custom",
        path=str(p),
        confidence="low",
        signals=[f"has:{f}" for f in hints] or ["no-known-signals"],
    )


def _claude_code_projects(base: Path) -> List[FrameworkDetection]:
    """Enumerate each Claude Code *project* as its own importable candidate.

    Claude Code is per-project (and per-session): one entry in
    ``~/.claude.json``'s ``projects`` map per project cwd, with that project's
    session transcripts under ``~/.claude/projects/<encoded-cwd>/``. The unit of
    "one imported agent" is therefore ONE project — its ``CLAUDE.md`` becomes
    the persona and all of its sessions fold into one starting Narrative. So we
    surface each project as a separate detection instead of a single global one.
    """
    claude_home = base / ".claude"
    claude_json = base / ".claude.json"
    if not claude_json.exists():
        return []
    try:
        data = json.loads(claude_json.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — a malformed config must not crash detect
        return []
    projects = data.get("projects")
    if not isinstance(projects, dict):
        return []
    from xyz_agent_context.migration.extractors import _encode_cwd

    out: List[FrameworkDetection] = []
    for cwd_str in projects:
        cwd = Path(cwd_str)
        has_md = (cwd / "CLAUDE.md").exists() or (cwd / "CLAUDE.local.md").exists()
        cwd_exists = cwd.exists()
        # Session transcripts live under projects/<encoded-cwd> — every
        # non-alphanumeric char becomes '-' (see extractors._encode_cwd).
        sess_dir = claude_home / "projects" / _encode_cwd(cwd)
        n_sessions = len(list(sess_dir.glob("*.jsonl"))) if sess_dir.exists() else 0
        # Denoise: the projects map holds EVERY dir ever opened in Claude Code.
        # Only surface one with real importable content — a CLAUDE.md or at least
        # one session. A bare "opened once" cwd with neither is dropped.
        if not (has_md or n_sessions >= 1):
            continue

        confidence: Confidence = "high" if has_md else ("medium" if (cwd_exists or n_sessions) else "low")
        signals = ["project"]
        if cwd_exists:
            signals.append("cwd-exists")
        if has_md:
            signals.append("has:CLAUDE.md")
        if n_sessions:
            signals.append(f"sessions:{n_sessions}")
        out.append(FrameworkDetection(
            framework="claude_code", path=cwd_str, confidence=confidence, signals=signals,
        ))

    # Rank: highest confidence first, then more sessions first (most-used projects).
    def _sess_count(d: FrameworkDetection) -> int:
        for s in d.signals:
            if s.startswith("sessions:"):
                return int(s.split(":", 1)[1])
        return 0

    out.sort(key=lambda d: (_ORDER[d.confidence], _sess_count(d)), reverse=True)
    return out


def detect_all(home: str | Path | None = None) -> List[FrameworkDetection]:
    """Probe the standard home locations for every framework. Returns highest-
    confidence hit per framework, so a machine with several frameworks yields
    several detections. Claude Code is expanded into one detection PER PROJECT
    (its natural agent boundary), with the global ``~/.claude`` config kept as a
    low-priority fallback for its shared skills + MCP."""
    base = Path(home).expanduser() if home else Path.home()
    out: List[FrameworkDetection] = []
    for fw, sig in _SIGNALS.items():
        best: Optional[FrameworkDetection] = None
        for d_name in sig["home_dirs"]:
            d = _score_dir(base / d_name, fw)
            if d and (best is None or _ORDER[d.confidence] > _ORDER[best.confidence]):
                best = d
        if best is None:
            continue

        if fw == "claude_code":
            projects = _claude_code_projects(base)
            out.extend(projects)
            # Keep the global ~/.claude entry as a fallback (shared skills+MCP,
            # no project persona/sessions) — demoted below the projects.
            best.confidence = "low"
            best.signals = ["global-shared-config", *best.signals]
            out.append(best)
        else:
            out.append(best)
    return out
