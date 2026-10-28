"""
@file_name: index.py
@author: Bin Liang
@date: 2026-09-03
@description: The official plugin index (``index.json``) and blocklist (``blocked_versions.json``), cached locally for a day.

The index holds metadata only (id, repo, author, description, tags, kinds);
inclusion is a metadata check, not a code review, and the UI says so. The
blocklist is consulted at boot (``discover``) and before install. Both are
fetched at most once per ``ttl`` and served from the cache when the network
is down — a missing network must not block the factory page.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import httpx
from loguru import logger

from narranexus.kernel.plugins.manifest import BUILTIN_PREFIX, PLUGIN_ID_RE

DEFAULT_INDEX_URL = "https://raw.githubusercontent.com/protagolabs/narranexus-plugins/main"
INDEX_FILE = "index.json"
BLOCKED_FILE = "blocked_versions.json"
DEFAULT_TTL_S = 24 * 3600
MAX_INDEX_BYTES = 5 * 1024 * 1024  # a redirect to a large file must not fill the cache dir


class IndexUnavailable(RuntimeError):
    """The index (or its blocklist) could not be obtained and nothing is cached."""


@dataclass(frozen=True)
class IndexEntry:
    id: str
    repo: str
    author: str = ""
    description: str = ""
    tags: tuple[str, ...] = ()
    kinds: tuple[str, ...] = ()


@dataclass
class Index:
    cache_dir: Path
    base_url: str = DEFAULT_INDEX_URL
    ttl_s: float = DEFAULT_TTL_S
    client: httpx.Client | None = None
    _entries: list[IndexEntry] = field(default_factory=list)
    _blocked: dict[str, dict[str, str]] = field(default_factory=dict)
    _blocked_known: bool = False
    _loaded: bool = False

    def _cache_path(self, name: str) -> Path:
        return self.cache_dir / name

    def _fresh(self, name: str) -> bool:
        p = self._cache_path(name)
        return p.is_file() and (time.time() - p.stat().st_mtime) < self.ttl_s

    def _fetch_file(self, name: str) -> Any:
        p = self._cache_path(name)
        if not self._fresh(name):
            try:
                from narranexus.kernel.plugins.install.sources import _http

                with _http(self.client) as client:
                    resp = client.get(f"{self.base_url}/{name}", timeout=20.0, follow_redirects=True)
                resp.raise_for_status()
                if len(resp.content) > MAX_INDEX_BYTES:
                    raise ValueError(f"{name} exceeds {MAX_INDEX_BYTES // (1024 * 1024)} MB")
                json.loads(resp.text)  # must be JSON before it replaces the cache
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_text(resp.text, encoding="utf-8")
            except (httpx.HTTPError, ValueError) as exc:
                logger.warning(f"[plugins] index {name} refresh failed ({exc}); using cache" if p.is_file() else f"[plugins] index {name} unavailable: {exc}")
        if not p.is_file():
            return None
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None

    def refresh(self, *, force: bool = False) -> None:
        if force:
            for name in (INDEX_FILE, BLOCKED_FILE):
                p = self._cache_path(name)
                if p.is_file():
                    p.unlink()
        raw = self._fetch_file(INDEX_FILE) or {}
        items = raw.get("plugins", raw) if isinstance(raw, dict) else raw
        entries: list[IndexEntry] = []
        for item in items if isinstance(items, list) else []:
            if not isinstance(item, Mapping) or "id" not in item or "repo" not in item:
                continue
            # The same rules examples/index-repo/validate_index.py enforces at
            # publish time, enforced again at read time: a polluted or
            # mis-merged index must not surface a malformed or builtin.* id.
            if not PLUGIN_ID_RE.match(str(item["id"])) or str(item["id"]).startswith(BUILTIN_PREFIX):
                logger.warning(f"[plugins] index entry {item['id']!r} ignored: not a valid third-party plugin id")
                continue
            entries.append(
                IndexEntry(
                    id=str(item["id"]),
                    repo=str(item["repo"]),
                    author=str(item.get("author", "")),
                    description=str(item.get("description", "")),
                    tags=tuple(str(t) for t in item.get("tags", []) or []),
                    kinds=tuple(str(k) for k in item.get("kinds", []) or []),
                )
            )
        self._entries = entries
        blocked = self._fetch_file(BLOCKED_FILE)
        # None = never obtained (no network AND no cache): the caller must
        # know the difference between "nothing is blocked" and "we could not
        # find out" — the old `or {}` erased it.
        self._blocked_known = blocked is not None
        blocked = blocked or {}
        self._blocked = {str(k): dict(v) for k, v in blocked.items() if isinstance(v, Mapping)} if isinstance(blocked, dict) else {}
        self._loaded = True

    def entries(self) -> tuple[IndexEntry, ...]:
        if not self._loaded:
            self.refresh()
        return tuple(self._entries)

    def blocked(self) -> dict[str, dict[str, str]]:
        """The blocklist. Raises IndexUnavailable when it was never obtained (no network and no cached copy) — a stale cache is still an answer."""
        if not self._loaded:
            self.refresh()
        if not self._blocked_known:
            raise IndexUnavailable(f"{self.base_url}/{BLOCKED_FILE}: never fetched and no cached copy")
        return dict(self._blocked)

    def cached_blocked(self) -> dict[str, dict[str, str]] | None:
        """The cached blocklist without any network (for boot: block a plugin the index withdrew since it was installed); None when there is no cache."""
        p = self._cache_path(BLOCKED_FILE)
        if not p.is_file():
            return None
        try:
            raw = json.loads(p.read_text(encoding="utf-8"))
        except ValueError:
            return None
        return {str(k): dict(v) for k, v in raw.items() if isinstance(v, Mapping)} if isinstance(raw, dict) else {}

    def search(self, query: str) -> tuple[IndexEntry, ...]:
        q = query.strip().lower()
        if not q:
            return self.entries()
        return tuple(
            e for e in self.entries()
            if q in e.id.lower() or q in e.description.lower() or any(q in t.lower() for t in e.tags) or any(q in k.lower() for k in e.kinds)
        )

    def get(self, plugin_id: str) -> IndexEntry | None:
        return next((e for e in self.entries() if e.id == plugin_id), None)


__all__ = ["BLOCKED_FILE", "DEFAULT_INDEX_URL", "DEFAULT_TTL_S", "INDEX_FILE", "MAX_INDEX_BYTES", "Index", "IndexEntry", "IndexUnavailable"]
