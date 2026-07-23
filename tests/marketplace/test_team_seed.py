"""
@file_name: test_team_seed.py
@author: NetMind.AI
@date: 2026-07-22
@description: Team Marketplace seed — the verify-then-store sha256 gate and
idempotency, which had zero coverage (the only network + hash-gated
component). httpx is stubbed so no real fetch happens.
"""

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from xyz_agent_context._skill_marketplace_impl.artifact_store import LocalArtifactStore
from xyz_agent_context.repository.team_catalog_repository import TeamCatalogRepository


def _bundle_bytes() -> bytes:
    import io

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format_version": "1.1"}))
    return buf.getvalue()


class _FakeResponse:
    def __init__(self, content: bytes):
        self.content = content

    def raise_for_status(self):
        return None


class _FakeAsyncClient:
    """Minimal async httpx.AsyncClient stand-in returning fixed bytes."""

    def __init__(self, content: bytes):
        self._content = content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        return _FakeResponse(self._content)


@pytest.fixture
def seed_env(db_client, tmp_path, monkeypatch):
    from xyz_agent_context.settings import settings

    monkeypatch.setattr(settings, "base_working_path", str(tmp_path / "workspaces"))
    monkeypatch.delenv("TEMPLATE_S3_BUCKET", raising=False)
    monkeypatch.delenv("SKILL_S3_BUCKET", raising=False)

    import xyz_agent_context.repository._team_marketplace_seed as seed_mod

    store = LocalArtifactStore(tmp_path / "team_store")
    monkeypatch.setattr(seed_mod, "get_template_store", lambda: store)

    content = _bundle_bytes()
    real_sha = hashlib.sha256(content).hexdigest()

    def _patch_seed_list(sha: str):
        monkeypatch.setattr(seed_mod, "SEED_TEMPLATES", [{
            "template_id": "seed-team", "name": "Seed Team", "description": "d",
            "categories": ["team"], "author": "NarraNexus team", "agent_count": 3,
            "source_url": "https://www.narra.nexus/templates/seed-team.nxbundle",
            "bundle_sha256": sha, "sort_order": 0,
        }])

    return {"mod": seed_mod, "store": store, "content": content,
            "real_sha": real_sha, "patch_list": _patch_seed_list, "monkeypatch": monkeypatch}


@pytest.mark.asyncio
async def test_seed_verifies_sha256_and_stores(db_client, seed_env):
    mp = seed_env["monkeypatch"]
    seed_env["patch_list"](seed_env["real_sha"])
    mp.setattr(seed_env["mod"].httpx, "AsyncClient", lambda **k: _FakeAsyncClient(seed_env["content"]))

    n = await seed_env["mod"].seed_team_marketplace(db_client)
    assert n == 1

    entry = await TeamCatalogRepository(db_client).get("seed-team")
    assert entry is not None and entry.bundle_sha256 == seed_env["real_sha"]
    assert seed_env["store"].exists(entry.store_key)


@pytest.mark.asyncio
async def test_seed_rejects_sha256_mismatch(db_client, seed_env):
    mp = seed_env["monkeypatch"]
    # Catalog claims a hash that does NOT match the (fake) fetched bytes.
    seed_env["patch_list"]("0" * 64)
    mp.setattr(seed_env["mod"].httpx, "AsyncClient", lambda **k: _FakeAsyncClient(seed_env["content"]))

    n = await seed_env["mod"].seed_team_marketplace(db_client)
    assert n == 0  # mismatch → skipped, not stored
    assert await TeamCatalogRepository(db_client).get("seed-team") is None


@pytest.mark.asyncio
async def test_seed_is_idempotent(db_client, seed_env):
    mp = seed_env["monkeypatch"]
    seed_env["patch_list"](seed_env["real_sha"])
    calls = {"n": 0}

    def _factory(**k):
        calls["n"] += 1
        return _FakeAsyncClient(seed_env["content"])

    mp.setattr(seed_env["mod"].httpx, "AsyncClient", _factory)
    await seed_env["mod"].seed_team_marketplace(db_client)
    first = calls["n"]
    await seed_env["mod"].seed_team_marketplace(db_client)
    # second pass finds it in catalog + store → no re-fetch
    assert calls["n"] == first
