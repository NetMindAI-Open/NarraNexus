"""
@file_name: _slack_skill_loader.py
@date: 2026-05-08
@description: Index `skills/*.md` at startup, serve method docs on demand.

The `skills/` directory is generated at build time from the Slack OpenAPI
spec (see `scripts/gen_slack_skills.py`). Each Slack Web API method has
its own ``{method}.md`` file. The loader keeps a filename → path map in
memory; doc content is read lazily by ``get(method)`` so we don't pay
for ~700 KB of doc text in every process.

Usage from ``slack_skill`` MCP tool:

    loader = get_skill_loader()
    return loader.get("chat.postMessage")

Pattern mirrors ``lark_module/_lark_skill_loader.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class SlackSkillLoader:
    """Index of Slack Web API method docs.

    File layout under ``skills/``:

      ``chat.postMessage.md``, ``conversations.history.md``, ...
      ``_index.json``  — category → [methods] (built by generator)

    Method names use literal dots so ``get("chat.postMessage")`` matches
    the filename exactly.
    """

    def __init__(self, skills_dir: Optional[Path] = None):
        self._dir = skills_dir or (Path(__file__).parent / "skills")
        self._index: dict[str, Path] = {}
        self._categories: dict[str, list[str]] = {}
        self._build_index()

    def _build_index(self) -> None:
        if not self._dir.exists():
            return
        for md in self._dir.glob("*.md"):
            stem = md.stem
            if stem.startswith("_"):  # _index, _README, etc.
                continue
            self._index[stem] = md

        # Optional category index (generator-provided)
        idx = self._dir / "_index.json"
        if idx.exists():
            try:
                self._categories = json.loads(idx.read_text())
            except (OSError, json.JSONDecodeError):
                self._categories = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def list_methods(self, prefix: str = "") -> list[str]:
        """Sorted list of methods optionally filtered by prefix (e.g. ``"chat."``)."""
        return sorted(m for m in self._index if m.startswith(prefix))

    def list_categories(self) -> list[str]:
        """Top-level categories (chat, conversations, users, ...)."""
        return sorted(self._categories.keys()) if self._categories else sorted(
            {m.split(".", 1)[0] for m in self._index}
        )

    def get(self, method: str) -> str:
        """Read the markdown doc for ``method``.

        Returns a friendly hint string when the method is unknown rather
        than raising — the agent's error path through ``slack_skill`` is
        meant to be self-correcting (read hint → guess closer name → retry).
        """
        path = self._index.get(method)
        if path:
            try:
                return path.read_text(encoding="utf-8")
            except OSError as e:  # pragma: no cover
                return f"Error reading skill file for '{method}': {e}"

        # Build a helpful hint from same-category methods
        category = method.split(".", 1)[0] if "." in method else method
        same_cat = self.list_methods(category + ".")[:8]
        if same_cat:
            return (
                f"Unknown method '{method}'. "
                f"Did you mean one of: {', '.join(same_cat)}?\n\n"
                f"Use `slack_skill` with the exact dotted name "
                f"(e.g. `chat.postMessage`)."
            )
        # No same-category — list categories
        cats = ", ".join(self.list_categories()[:12])
        return (
            f"Unknown method '{method}'. "
            f"Available categories include: {cats}.\n\n"
            f"Methods are dotted names like `chat.postMessage`, `users.info`."
        )


# ----------------------------------------------------------------------
# Module-level singleton (lazy init)
# ----------------------------------------------------------------------

_loader: Optional[SlackSkillLoader] = None


def get_skill_loader() -> SlackSkillLoader:
    """Singleton accessor. First call indexes the skills dir; subsequent
    calls return the cached loader."""
    global _loader
    if _loader is None:
        _loader = SlackSkillLoader()
    return _loader
