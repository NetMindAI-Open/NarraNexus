"""
@file_name: _telegram_skill_loader.py
@date: 2026-05-09
@description: Index `skills/*.md` at startup, serve method docs on demand.

The ``skills/`` directory is hand-curated for Telegram (Bot API has no
official OpenAPI). Each method has its own ``{method}.md`` file. The
loader keeps a filename → path map; doc content is read lazily by
``get(method)`` so we don't pay for the full set of docs in every
process.

Usage from ``tg_skill`` MCP tool:

    loader = get_skill_loader()
    return loader.get("sendMessage")

Pattern mirrors ``slack_module/_slack_skill_loader.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional


class TelegramSkillLoader:
    """Index of Telegram Bot API method docs.

    Method names are camelCase — no dots, unlike Slack. Filenames match
    method names exactly: ``sendMessage.md``, ``getUpdates.md``, etc.
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
            if stem.startswith("_"):
                continue
            self._index[stem] = md

        idx = self._dir / "_index.json"
        if idx.exists():
            try:
                self._categories = json.loads(idx.read_text())
            except (OSError, json.JSONDecodeError):
                self._categories = {}

    def list_methods(self, prefix: str = "") -> list[str]:
        return sorted(m for m in self._index if m.startswith(prefix))

    def list_categories(self) -> list[str]:
        return sorted(self._categories.keys()) if self._categories else []

    def get(self, method: str) -> str:
        path = self._index.get(method)
        if path:
            try:
                return path.read_text(encoding="utf-8")
            except OSError as e:  # pragma: no cover
                return f"Error reading skill file for '{method}': {e}"

        # Helpful hint when method isn't in our hand-curated set.
        # (Telegram has ~100 methods; we cover the high-traffic ~25.)
        cats = ", ".join(self.list_categories()) if self._categories else "n/a"
        return (
            f"Unknown method '{method}'. Categories: {cats}.\n\n"
            f"Telegram has ~100 Bot API methods total; this loader carries "
            f"the high-traffic ~25 (text + admin + chat info). For methods "
            f"not in this set (especially media: sendPhoto / sendDocument / "
            f"sendVoice / etc.), consult https://core.telegram.org/bots/api "
            f"and call the method directly via tg_cli — it works for any "
            f"valid Bot API method, just without local docs."
        )


_loader: Optional[TelegramSkillLoader] = None


def get_skill_loader() -> TelegramSkillLoader:
    global _loader
    if _loader is None:
        _loader = TelegramSkillLoader()
    return _loader
