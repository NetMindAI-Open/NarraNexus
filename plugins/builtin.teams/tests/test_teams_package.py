"""
@file_name: test_teams_package.py
@author: Bin Liang
@date: 2026-09-04
@description: Package contract of `builtin.teams`: the manifest on disk is the plugin's own (the kernel reads it, holds no copy), every provides ref resolves inside the package, and the post-start hook seeds only on a registry host.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_matches_host_and_refs_stay_in_package():
    from narranexus.kernel.plugins.loader import resolve_symbol

    on_disk = json.loads((ROOT / "narranexus-plugin.json").read_text())
    for refs in on_disk["provides"].values():
        for ref in refs:
            assert ref.startswith("narranexus_plugins.teams."), ref
            assert resolve_symbol(ref)


@pytest.mark.asyncio
async def test_seed_hook_skips_a_non_registry_host(monkeypatch):
    from narranexus.platform.marketplace import skill_marketplace_service
    from narranexus_plugins.teams import plugin_hooks

    monkeypatch.setattr(skill_marketplace_service, "is_registry_host", lambda: False)
    assert await plugin_hooks.seed_team_marketplace_on_start.fn(db=object()) == 0


@pytest.mark.asyncio
async def test_seed_hook_seeds_a_registry_host(monkeypatch):
    from narranexus.platform.marketplace import skill_marketplace_service
    from narranexus_plugins.teams import marketplace_seed, plugin_hooks

    monkeypatch.setattr(skill_marketplace_service, "is_registry_host", lambda: True)
    seen = {}

    async def fake_seed(db):
        seen["db"] = db
        return 3

    monkeypatch.setattr(marketplace_seed, "seed_team_marketplace", fake_seed)
    db = object()
    assert await plugin_hooks.seed_team_marketplace_on_start.fn(db=db) == 3 and seen["db"] is db
