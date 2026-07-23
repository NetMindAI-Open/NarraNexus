"""
@file_name: test_team_marketplace.py
@author: NetMind.AI
@date: 2026-07-21
@description: Team Marketplace catalog + store + service + API tests.

Covers: TeamCatalogRepository (upsert/list_enabled ordering/delete/downloads),
get_template_store separation from skills, publish (blob to store + catalog
row), install_preflight (resolve → sha256 verify → importer.preflight, with
tamper abort), and the /api/marketplace/teams/* routes (list/detail/download/
publish/delete). The importer is stubbed where a real bundle isn't needed.
"""

import hashlib

import json
import zipfile
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from httpx import ASGITransport

from xyz_agent_context._skill_marketplace_impl.artifact_store import (
    LocalArtifactStore,
    get_artifact_store,
    get_template_store,
)
from xyz_agent_context.repository.team_catalog_repository import TeamCatalogRepository
from xyz_agent_context.schema.team_marketplace_schema import TeamTemplate

USER_ID = "usr_test"


def _fake_bundle(tmp_path: Path, name: str = "t.nxbundle") -> Path:
    """A .nxbundle is a zip; content need only be a valid zip for store tests."""
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("manifest.json", json.dumps({"format_version": "1.1"}))
    return p


def _tmpl(template_id: str, **over) -> TeamTemplate:
    payload = dict(
        template_id=template_id, name=template_id.replace("-", " ").title(),
        description="d", categories=["team"], agent_count=3,
        store_key=f"{template_id}/abc/{template_id}.nxbundle",
        bundle_sha256="deadbeef", sort_order=0,
    )
    payload.update(over)
    return TeamTemplate(**payload)


# ---------------------------------------------------------------------------
# Repository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_upsert_and_list_enabled_ordering(db_client):
    repo = TeamCatalogRepository(db_client)
    await repo.save_template(_tmpl("b-team", sort_order=2))
    await repo.save_template(_tmpl("a-team", sort_order=1))
    await repo.save_template(_tmpl("hidden", sort_order=0, enabled=False))

    enabled = await repo.list_enabled()
    assert [t.template_id for t in enabled] == ["a-team", "b-team"]
    assert len(await repo.list_all()) == 3


@pytest.mark.asyncio
async def test_upsert_is_idempotent_and_preserves_downloads(db_client):
    repo = TeamCatalogRepository(db_client)
    await repo.save_template(_tmpl("t", agent_count=3))
    await repo.increment_downloads("t")
    await repo.save_template(_tmpl("t", agent_count=5, description="updated"))

    got = await repo.get("t")
    assert got.agent_count == 5
    assert got.description == "updated"
    assert got.downloads == 1  # not reset on re-publish
    assert got.categories == ["team"]


@pytest.mark.asyncio
async def test_delete(db_client):
    repo = TeamCatalogRepository(db_client)
    await repo.save_template(_tmpl("t"))
    assert await repo.remove("t") == 1
    assert await repo.get("t") is None


# ---------------------------------------------------------------------------
# Store separation
# ---------------------------------------------------------------------------


def test_template_store_is_separate_from_skills(tmp_path, monkeypatch):
    from xyz_agent_context.settings import settings

    monkeypatch.setattr(settings, "base_working_path", str(tmp_path / "workspaces"))
    monkeypatch.delenv("SKILL_S3_BUCKET", raising=False)
    monkeypatch.delenv("TEMPLATE_S3_BUCKET", raising=False)

    skill_store = get_artifact_store()
    team_store = get_template_store()
    assert isinstance(skill_store, LocalArtifactStore)
    assert isinstance(team_store, LocalArtifactStore)
    # teams live in a subfolder, never colliding with skill keys
    assert team_store.root != skill_store.root
    assert "teams" in str(team_store.root)


def test_template_store_s3_prefix(monkeypatch):
    from xyz_agent_context._skill_marketplace_impl.artifact_store import S3ArtifactStore

    monkeypatch.setenv("TEMPLATE_S3_BUCKET", "my-bucket")
    store = get_template_store()
    assert isinstance(store, S3ArtifactStore)
    assert store.prefix == "narranexus-teams"


# ---------------------------------------------------------------------------
# Service: publish + install_preflight
# ---------------------------------------------------------------------------


@pytest.fixture
def service(db_client, tmp_path, monkeypatch):
    import xyz_agent_context.team_marketplace_service as mod

    monkeypatch.setattr(mod, "get_deployment_mode", lambda: "cloud")
    store = LocalArtifactStore(tmp_path / "team_store")
    return mod.TeamMarketplaceService(db_client=db_client, store=store)


@pytest.mark.asyncio
async def test_publish_stores_blob_and_row(service, db_client, tmp_path):
    bundle = _fake_bundle(tmp_path)
    entry = await service.publish(
        bundle, template_id="finance-team", name="Finance Team",
        description="6 agents", categories=["finance", "team"], agent_count=6,
    )
    assert entry.template_id == "finance-team"
    assert entry.bundle_sha256 == hashlib.sha256(bundle.read_bytes()).hexdigest()
    assert service._store_ref().exists(entry.store_key)

    listing = await service.list_templates()
    assert listing["templates"][0]["template_id"] == "finance-team"
    assert listing["templates"][0]["agent_count"] == 6


@pytest.mark.asyncio
async def test_install_preflight_verifies_and_calls_importer(service, tmp_path, monkeypatch):
    bundle = _fake_bundle(tmp_path)
    await service.publish(bundle, template_id="t", name="T", agent_count=2)

    captured = {}

    async def _fake_preflight(path, user_id):
        captured["path"] = Path(path)
        captured["user_id"] = user_id
        captured["bytes"] = Path(path).read_bytes()
        return {"preflight_token": "tok_1", "manifest": {"team": {"name": "T"}},
                "name_clashes": [], "team_clash": None, "credential_clashes": []}

    import xyz_agent_context.bundle.importer as importer

    monkeypatch.setattr(importer, "preflight", _fake_preflight)

    result = await service.install_preflight("t", USER_ID)
    assert result["preflight_token"] == "tok_1"
    assert captured["user_id"] == USER_ID
    # the importer received exactly the published bundle bytes
    assert captured["bytes"] == bundle.read_bytes()
    # download counter bumped
    entry = await TeamCatalogRepository(service._db_client).get("t")
    assert entry.downloads == 1


@pytest.mark.asyncio
async def test_install_preflight_tamper_aborts(service, tmp_path, monkeypatch):
    bundle = _fake_bundle(tmp_path)
    entry = await service.publish(bundle, template_id="t", name="T")
    # Overwrite the stored blob with different content (sha mismatch).
    tampered = _fake_bundle(tmp_path, "evil.nxbundle")
    with zipfile.ZipFile(tampered, "a") as zf:
        zf.writestr("extra.txt", "x")
    service._store_ref().put_file(entry.store_key, tampered)

    import xyz_agent_context.bundle.importer as importer

    async def _fail(*a, **k):
        raise AssertionError("importer must not run on tampered bundle")

    monkeypatch.setattr(importer, "preflight", _fail)
    with pytest.raises(ValueError, match="sha256 mismatch"):
        await service.install_preflight("t", USER_ID)


@pytest.mark.asyncio
async def test_install_preflight_missing_template(service):
    with pytest.raises(FileNotFoundError):
        await service.install_preflight("nope", USER_ID)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@pytest.fixture
def app(db_client, tmp_path, monkeypatch):
    from xyz_agent_context.settings import settings

    monkeypatch.setattr(settings, "base_working_path", str(tmp_path / "workspaces"))
    monkeypatch.delenv("SKILL_S3_BUCKET", raising=False)
    monkeypatch.delenv("TEMPLATE_S3_BUCKET", raising=False)

    import xyz_agent_context.team_marketplace_service as svc_mod

    monkeypatch.setattr(svc_mod, "get_deployment_mode", lambda: "cloud")

    async def _get_db():
        return db_client

    import xyz_agent_context.utils.db_factory as db_factory

    monkeypatch.setattr(db_factory, "get_db_client", _get_db)

    import backend.routes.marketplace_teams as routes

    async def _fake_user(request):
        return USER_ID

    monkeypatch.setattr(routes, "resolve_current_user_id", _fake_user)
    # local publisher gate (no staff role in tests)
    import xyz_agent_context.utils.deployment_mode as dm

    monkeypatch.setattr(dm, "is_cloud_mode", lambda: False)

    app = FastAPI()
    app.include_router(routes.router, prefix="/api/marketplace/teams")
    return app


async def _client(app):
    return httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://t")


@pytest.mark.asyncio
async def test_routes_publish_list_detail_download_delete(app, tmp_path):
    bundle = _fake_bundle(tmp_path)
    async with await _client(app) as ac:
        pub = await ac.post(
            "/api/marketplace/teams/templates",
            files={"file": ("t.nxbundle", bundle.read_bytes())},
            data={"template_id": "finance-team", "name": "Finance Team",
                  "categories": "finance,team", "agent_count": "6"},
        )
        assert pub.status_code == 200, pub.text
        assert pub.json()["template_id"] == "finance-team"

        lst = await ac.get("/api/marketplace/teams/templates")
        assert lst.json()["templates"][0]["agent_count"] == 6

        det = await ac.get("/api/marketplace/teams/templates/finance-team")
        assert det.json()["template_id"] == "finance-team"

        dl = await ac.get("/api/marketplace/teams/templates/finance-team/download")
        assert dl.status_code == 200
        assert dl.headers["X-Bundle-Sha256"] == hashlib.sha256(bundle.read_bytes()).hexdigest()
        assert dl.content[:2] == b"PK"

        missing = await ac.get("/api/marketplace/teams/templates/nope")
        assert missing.status_code == 404

        deleted = await ac.delete("/api/marketplace/teams/templates/finance-team")
        assert deleted.status_code == 200
        assert (await ac.get("/api/marketplace/teams/templates/finance-team")).status_code == 404
