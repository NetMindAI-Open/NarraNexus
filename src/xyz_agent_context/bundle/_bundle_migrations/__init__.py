"""
@file_name: _bundle_migrations/__init__.py
@author: NetMind.AI
@date: 2026-05-08
@description: Bundle format version migrations (PRD §8.6)

When the on-disk bundle format changes incompatibly (a new major version),
import-side migrations are placed under this package to upgrade an older
bundle's manifest + extracted tree in-place to the current major.

Naming convention:
    migrations.py contains a single migration:
        async def migrate_v1_to_v2(work_dir: Path, manifest: dict) -> dict
    plus a registry mapping (from_major, to_major) → callable.

Apply via `apply_migrations(work_dir, manifest)` — chains migrations until
the manifest's major matches CURRENT_BUNDLE_MAJOR.

When v2 ships, drop a `v1_to_v2.py` next to this __init__.py and register
it in MIGRATIONS below.
"""

from __future__ import annotations

from pathlib import Path
from typing import Awaitable, Callable, Dict, Tuple


CURRENT_BUNDLE_MAJOR = 1


# (from_major, to_major) → async migration callable
MIGRATIONS: Dict[Tuple[int, int], Callable[[Path, dict], Awaitable[dict]]] = {}


def parse_major(version: str) -> int:
    try:
        return int((version or "0").split(".")[0])
    except (ValueError, AttributeError):
        return 0


async def apply_migrations(work_dir: Path, manifest: dict) -> dict:
    """Walk the migration chain until manifest is at CURRENT_BUNDLE_MAJOR.

    Each migration MAY mutate the extracted tree under work_dir AND/OR
    return a new manifest dict. It MUST update manifest['bundle_format_version']
    to the target major.major form.

    Raises ValueError if no migration path exists.
    """
    cur = parse_major(manifest.get("bundle_format_version", "0"))
    while cur < CURRENT_BUNDLE_MAJOR:
        next_major = cur + 1
        key = (cur, next_major)
        if key not in MIGRATIONS:
            raise ValueError(
                f"No bundle migration registered for {cur} → {next_major}. "
                f"This bundle is too old to import on the current NarraNexus instance."
            )
        manifest = await MIGRATIONS[key](work_dir, manifest)
        cur = parse_major(manifest.get("bundle_format_version", "0"))
        if cur != next_major:
            raise ValueError(
                f"Migration {key} did not bump bundle_format_version to {next_major} "
                f"(got {manifest.get('bundle_format_version')!r})"
            )
    if cur > CURRENT_BUNDLE_MAJOR:
        raise ValueError(
            f"Bundle is from a newer NarraNexus version (major={cur}, "
            f"this instance supports up to {CURRENT_BUNDLE_MAJOR}). "
            "Upgrade NarraNexus or ask the bundle author to re-export with an older format."
        )
    return manifest
