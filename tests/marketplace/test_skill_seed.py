"""
@file_name: test_skill_seed.py
@author: NetMind.AI
@date: 2026-07-22
@description: Skill Marketplace seed — publishes the repo-vendored first-party
skills (marketplace_skills/) into the catalog + store, idempotently, with the
default flag preserved so agent-creation auto-install picks them up.
"""

import json
from pathlib import Path

import pytest

import xyz_agent_context.marketplace._skill_marketplace_impl.secret_box as secret_box_module
from xyz_agent_context.marketplace._skill_marketplace_impl.artifact_store import LocalArtifactStore
from xyz_agent_context.marketplace._skill_marketplace_seed import (
    _skills_root,
    seed_skill_marketplace,
)
from xyz_agent_context.repository.skill_catalog_repository import SkillCatalogRepository


def _fixture_skills(tmp_path: Path) -> Path:
    """A tiny marketplace_skills/ tree: one default skill + one normal."""
    root = tmp_path / "marketplace_skills"
    for name, default, ver in (("nx-vision", True, "1.0.0"), ("nx-utility", False, "0.9.0")):
        d = root / name
        (d / "scripts").mkdir(parents=True)
        (d / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: {name} skill\nversion: {ver}\n---\nBody.\n"
        )
        (d / "manifest.json").write_text(json.dumps(
            {"id": name, "version": ver, "capabilities": [], "default": default}
        ))
        (d / "scripts" / "run.py").write_text("x = 1\n")
    return root


@pytest.fixture
def seeded_env(db_client, tmp_path, monkeypatch):
    from xyz_agent_context.settings import settings

    monkeypatch.setattr(settings, "base_working_path", str(tmp_path / "workspaces"))
    monkeypatch.delenv("SKILL_S3_BUCKET", raising=False)
    monkeypatch.delenv("SKILL_SECRETS_KEY", raising=False)
    monkeypatch.setattr(secret_box_module, "_default_box", None)
    monkeypatch.setenv("MARKETPLACE_SKILLS_DIR", str(_fixture_skills(tmp_path)))

    # RegistryService(db) uses get_artifact_store(); point it at a temp store.
    store = LocalArtifactStore(tmp_path / "skill_store")
    import xyz_agent_context.marketplace._skill_marketplace_impl.registry as reg

    monkeypatch.setattr(reg, "get_artifact_store", lambda: store)
    return {"store": store}


@pytest.mark.asyncio
async def test_seed_publishes_repo_skills_with_default_flag(db_client, seeded_env):
    n = await seed_skill_marketplace(db_client)
    assert n == 2

    catalog = SkillCatalogRepository(db_client)
    vision = await catalog.get_version("nx-vision", "1.0.0")
    utility = await catalog.get_version("nx-utility", "0.9.0")
    assert vision is not None and vision.is_default is True
    assert utility is not None and utility.is_default is False
    assert seeded_env["store"].exists(vision.s3_key)

    # default listing (drives agent-creation auto-install) sees only the default
    defaults = await catalog.list_defaults()
    assert [d.skill_id for d in defaults] == ["nx-vision"]


@pytest.mark.asyncio
async def test_seed_is_idempotent(db_client, seeded_env):
    await seed_skill_marketplace(db_client)
    v1 = await SkillCatalogRepository(db_client).get_version("nx-vision", "1.0.0")
    # second pass re-publishes nothing (same version already in store)
    n = await seed_skill_marketplace(db_client)
    assert n == 2  # counted as present, not re-published
    v2 = await SkillCatalogRepository(db_client).get_version("nx-vision", "1.0.0")
    assert v2.package_hash == v1.package_hash


@pytest.mark.asyncio
async def test_seed_noop_when_dir_missing(db_client, monkeypatch):
    monkeypatch.setenv("MARKETPLACE_SKILLS_DIR", "/nonexistent/path/xyz")
    assert await seed_skill_marketplace(db_client) == 0


def test_real_repo_skills_root_exists_and_has_defaults():
    """The real marketplace_skills/ ships the two NetMind default skills."""
    root = _skills_root()
    assert root is not None, "marketplace_skills/ must exist in the repo"
    names = {p.name for p in root.iterdir() if p.is_dir()}
    assert {"netmind-vision", "netmind-transcribe"} <= names
    for name in ("netmind-vision", "netmind-transcribe"):
        manifest = json.loads((root / name / "manifest.json").read_text())
        assert manifest.get("default") is True, f"{name} must be a default skill"
