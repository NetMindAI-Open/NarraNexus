"""
@file_name: _lark_skill_loader.py
@date: 2026-04-22
@description: Load Lark CLI Skill files (SKILL.md + references/ + routes/ +
scenes/ + any data file) for the `lark_skill` MCP tool.

Skills are installed via `npx skills add larksuite/cli -y -g` and live at
~/.agents/skills/lark-*/ (with a symlink at ~/.claude/skills/lark-*/). The
Agent reads these on-demand through the MCP tool — it cannot reach the
files via the filesystem tools (Read/Glob/Grep) because the files live
outside the per-agent workspace sandbox.

The loader rewrites markdown cross-references inside returned content so
the Agent is told to call `lark_skill(..., path="...")` again for any
linked file, never the `Read` tool.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

from loguru import logger

# Directories to search for Skill docs (in priority order)
_SKILL_SEARCH_PATHS = [
    Path.home() / ".claude" / "skills",
    Path.home() / ".agents" / "skills",
]

# Banner prepended to every markdown returned from `lark_skill`. Reminds
# the Agent that internal links have been rewritten to MCP-tool calls, so
# it doesn't fall back to the `Read` tool (which cannot see skill files).
_MARKDOWN_BANNER = (
    "> ⚠️ **Served by the `lark_skill` MCP tool.** All Lark skill files "
    "(SKILL.md, references/*, routes/*, scenes/*, schemas, …) live inside "
    "the MCP container. The `Read` / `Glob` / `Grep` tools **cannot** see "
    "them. Any link below that points to another file has been rewritten "
    "into a `lark_skill(agent_id, \"<skill>\", path=\"<file>\")` call — "
    "use that, don't try to Read the path.\n\n"
)

# Matches markdown links of the form `[text](target)` where target is a
# relative path ending in a file extension we care about. Intentionally
# does NOT match absolute URLs (http://, https://, mailto:) or anchors.
_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)\s#]+?\.(?:md|xml|json|yaml|yml|txt))(?:#[^)]*)?\)")

# Matches a lark cross-skill path like "../lark-shared/SKILL.md" →
# group(1)="lark-shared", group(2)="SKILL.md"
_CROSS_SKILL_RE = re.compile(r"^\.\./(lark-[a-zA-Z0-9_-]+)/(.+)$")


def _find_skill_dirs() -> dict[str, Path]:
    """Discover lark-* skill directories. Returns {name: dir_path}."""
    skills: dict[str, Path] = {}
    env_dir = os.environ.get("LARK_SKILLS_DIR")
    search_paths = list(_SKILL_SEARCH_PATHS)
    if env_dir:
        search_paths.insert(0, Path(env_dir))

    for base in search_paths:
        if not base.is_dir():
            continue
        for child in sorted(base.iterdir()):
            if child.is_dir() and child.name.startswith("lark-"):
                skill_file = child / "SKILL.md"
                if skill_file.is_file():
                    name = child.name
                    if name not in skills:  # First found wins
                        skills[name] = child
    return skills


def _strip_frontmatter(content: str) -> str:
    """Remove YAML frontmatter (--- ... ---) from markdown."""
    if content.startswith("---"):
        match = re.match(r"^---\n.*?\n---\n?", content, re.DOTALL)
        if match:
            return content[match.end():].lstrip()
    return content


def _rewrite_markdown_links(content: str, current_skill: str) -> str:
    """Rewrite in-document links to `lark_skill(...)` calls.

    - `[text](references/foo.md)` → `[text — call lark_skill(agent_id, "<current>", path="references/foo.md")](...)`
    - `[text](routes/foo.md)` → same pattern, path="routes/foo.md"
    - `[text](scenes/foo.md)` → same pattern, path="scenes/foo.md"
    - `[text](../lark-other/SKILL.md)` → `[text — call lark_skill(agent_id, "lark-other", path="SKILL.md")](...)`

    The original `](target)` is preserved so the markdown still renders
    cleanly if humans view the text, but the visible hint next to `text`
    teaches the Agent the exact MCP call to make.
    """
    def repl(match: re.Match[str]) -> str:
        label = match.group(1)
        target = match.group(2)

        cross = _CROSS_SKILL_RE.match(target)
        if cross:
            other_skill = cross.group(1)
            inner_path = cross.group(2)
            hint = f'call `lark_skill(agent_id, "{other_skill}", path="{inner_path}")`'
        elif target.startswith("./") or not target.startswith(("/", "..")):
            # Within-skill relative link
            normalized = target[2:] if target.startswith("./") else target
            hint = f'call `lark_skill(agent_id, "{current_skill}", path="{normalized}")`'
        else:
            # Parent-escape or absolute — don't rewrite; Agent shouldn't follow
            return match.group(0)

        return f"[{label} — {hint}]({target})"

    return _MD_LINK_RE.sub(repl, content)


def _resolve_safe(skill_dir: Path, rel_path: str) -> Optional[Path]:
    """Resolve `rel_path` under `skill_dir` and reject anything outside.

    Returns the resolved absolute Path, or None if the path escapes the
    skill directory or the target is missing / not a regular file.
    """
    candidate = (skill_dir / rel_path).resolve(strict=False)
    try:
        candidate.relative_to(skill_dir.resolve(strict=False))
    except ValueError:
        logger.warning(
            f"Rejecting skill path traversal: skill={skill_dir.name} path={rel_path!r}"
        )
        return None
    if not candidate.is_file():
        return None
    return candidate


def get_available_skills() -> list[str]:
    """Return list of available skill names (e.g. ['lark-im', 'lark-calendar'])."""
    return sorted(_find_skill_dirs().keys())


def load_skill_file(name: str, path: str = "SKILL.md") -> Optional[str]:
    """Load a file from a Lark CLI skill pack.

    - `path` is relative to the skill directory. Default is the top-level
      `SKILL.md` index page. `references/foo.md`, `routes/mermaid.md`,
      `scenes/funnel.md`, and binary-safe `references/*.xml` data files
      are all supported.
    - Path traversal (`..`) is rejected.
    - Markdown files get frontmatter stripped, internal links rewritten
      into `lark_skill(...)` MCP call hints, and a banner prepended.
    - Non-markdown files are returned verbatim.

    Returns the content string, or None if not found / blocked.
    """
    dirs = _find_skill_dirs()
    skill_dir = dirs.get(name)
    if not skill_dir:
        return None

    target = _resolve_safe(skill_dir, path)
    if target is None:
        return None

    try:
        raw = target.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning(f"Failed to read skill file {name}/{path}: {e}")
        return None

    if target.suffix.lower() == ".md":
        body = _strip_frontmatter(raw)
        rewritten = _rewrite_markdown_links(body, current_skill=name)
        return _MARKDOWN_BANNER + rewritten

    # Non-markdown (XML schemas, JSON samples, etc.) — return verbatim.
    return raw


def get_all_skills() -> dict[str, str]:
    """Load all top-level SKILL.md contents. Returns {name: content}."""
    result = {}
    for name in _find_skill_dirs():
        content = load_skill_file(name, "SKILL.md")
        if content:
            result[name] = content
    return result
