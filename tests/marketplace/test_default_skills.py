"""
@file_name: test_default_skills.py
@author: NetMind.AI
@date: 2026-07-21
@description: Default-skill mechanism + platform env injection tests.

Covers: manifest "default": true -> catalog is_default; list_defaults
returns only the latest published version; SkillMarketplaceService
.install_defaults (installs, skips installed, degrades on unreachable
registry); NETMIND_API_KEY runtime injection from user_providers with
explicit-config precedence and no persistence; env_configured treating
platform vars as satisfied.
"""

import json
import zipfile
from pathlib import Path

import pytest

import xyz_agent_context._skill_marketplace_impl.secret_box as secret_box_module
from xyz_agent_context._skill_marketplace_impl.artifact_store import LocalArtifactStore
from xyz_agent_context._skill_marketplace_impl.registry import (
    LocalMarketplaceSource,
    RegistryService,
)
from xyz_agent_context.module.skill_module import SkillModule

AGENT_ID = "agt_test"
USER_ID = "usr_test"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    from xyz_agent_context.settings import settings

    monkeypatch.setattr(settings, "base_working_path", str(tmp_path / "workspaces"))
    monkeypatch.delenv("SKILL_SECRETS_KEY", raising=False)
    monkeypatch.delenv("SKILL_S3_BUCKET", raising=False)
    monkeypatch.setattr(secret_box_module, "_default_box", None)

    async def _noop_backup(**kwargs):
        return None

    import xyz_agent_context.bundle.skill_backup as skill_backup

    monkeypatch.setattr(skill_backup, "backup_after_api_install", _noop_backup)
    return tmp_path


def _make_zip(tmp_path, name, version="1.0.0", default=True, requires_env=None):
    env_line = ""
    if requires_env:
        env_line = (
            "metadata:\n  clawdbot:\n    requires:\n"
            f"      env: [{', '.join(requires_env)}]\n"
        )
    zip_path = tmp_path / f"{name}-{version}.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            f"{name}/SKILL.md",
            f"---\nname: {name}\ndescription: d\nversion: {version}\n{env_line}---\nBody.\n",
        )
        zf.writestr(
            f"{name}/manifest.json",
            json.dumps({"id": name, "version": version, "default": default}),
        )
    return zip_path


def _registry(db_client, tmp_path):
    return RegistryService(db_client, store=LocalArtifactStore(tmp_path / "store"))


def _service(db_client, registry, monkeypatch):
    import xyz_agent_context.skill_marketplace_service as service_module

    monkeypatch.setattr(service_module, "get_deployment_mode", lambda: "cloud")
    service = service_module.SkillMarketplaceService(db_client=db_client)

    async def _fixed_registry():
        return registry

    monkeypatch.setattr(service, "_registry", _fixed_registry)
    return service


# ---------------------------------------------------------------------------
# is_default catalog flag
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_manifest_default_flag_lands_in_catalog(db_client, workspace, tmp_path):
    registry = _registry(db_client, tmp_path)
    entry = await registry.publish(_make_zip(tmp_path, "netmind-vision"), "team")
    assert entry.is_default is True

    entry2 = await registry.publish(
        _make_zip(tmp_path, "ordinary-skill", default=False), "team"
    )
    assert entry2.is_default is False

    defaults = await registry.catalog.list_defaults()
    assert [e.skill_id for e in defaults] == ["netmind-vision"]


@pytest.mark.asyncio
async def test_list_defaults_latest_version_only(db_client, workspace, tmp_path):
    registry = _registry(db_client, tmp_path)
    await registry.publish(_make_zip(tmp_path, "netmind-vision", version="1.0.0"), "team")
    await registry.publish(_make_zip(tmp_path, "netmind-vision", version="1.1.0"), "team")

    defaults = await registry.catalog.list_defaults()
    assert len(defaults) == 1
    assert defaults[0].version == "1.1.0"


# ---------------------------------------------------------------------------
# install_defaults
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_install_defaults_installs_and_skips(db_client, workspace, tmp_path, monkeypatch):
    registry = _registry(db_client, tmp_path)
    await registry.publish(_make_zip(tmp_path, "netmind-vision"), "team")
    await registry.publish(_make_zip(tmp_path, "netmind-transcribe"), "team")
    service = _service(db_client, registry, monkeypatch)

    summary = await service.install_defaults(AGENT_ID, USER_ID)
    assert sorted(summary["installed"]) == ["netmind-transcribe", "netmind-vision"]
    assert summary["failed"] == []

    names = {s.name for s in SkillModule(agent_id=AGENT_ID, user_id=USER_ID).list_skills()}
    assert {"netmind-vision", "netmind-transcribe"} <= names

    again = await service.install_defaults(AGENT_ID, USER_ID)
    assert again["installed"] == []
    assert sorted(again["skipped"]) == ["netmind-transcribe", "netmind-vision"]


@pytest.mark.asyncio
async def test_install_defaults_registry_unreachable_degrades(db_client, workspace, monkeypatch):
    import xyz_agent_context.skill_marketplace_service as service_module

    monkeypatch.setattr(service_module, "get_deployment_mode", lambda: "local")
    from xyz_agent_context.settings import settings

    monkeypatch.delenv("SKILL_MARKETPLACE_LOCAL_REGISTRY", raising=False)
    monkeypatch.setattr(settings, "skill_marketplace_local_registry", False)
    service = service_module.SkillMarketplaceService(db_client=db_client)

    class _DeadRemote:
        async def list_defaults(self):
            raise ConnectionError("registry down")

    monkeypatch.setattr(service, "_remote", lambda: _DeadRemote())
    summary = await service.install_defaults(AGENT_ID, USER_ID)
    assert summary["registry_unreachable"] is True
    assert summary["installed"] == []


# ---------------------------------------------------------------------------
# Platform env injection (NETMIND_API_KEY)
# ---------------------------------------------------------------------------


async def _install_netmind_skill(db_client, tmp_path, monkeypatch):
    registry = _registry(db_client, tmp_path)
    await registry.publish(
        _make_zip(tmp_path, "netmind-vision", requires_env=["NETMIND_API_KEY"]), "team"
    )
    from xyz_agent_context._skill_marketplace_impl.install_pipeline import InstallPipeline

    pipeline = InstallPipeline(AGENT_ID, USER_ID, db_client=db_client)
    await pipeline.install_from_marketplace(
        "netmind-vision", marketplace_source=LocalMarketplaceSource(registry)
    )


@pytest.mark.asyncio
async def test_netmind_key_injected_from_provider_config(db_client, workspace, tmp_path, monkeypatch):
    await _install_netmind_skill(db_client, tmp_path, monkeypatch)
    await db_client.insert(
        "user_providers",
        {"user_id": USER_ID, "source": "netmind", "protocol": "openai", "name": "NetMind",
         "provider_id": "netmind-openai", "api_key": "nm-secret-123",
         "base_url": "https://api.netmind.ai/inference-api/openai/v1"},
    )

    module = SkillModule(agent_id=AGENT_ID, user_id=USER_ID, database_client=db_client)
    skills = module.list_skills()
    env = await module._resolve_platform_env(module.get_all_skill_env_vars(), skills)
    assert env["NETMIND_API_KEY"] == "nm-secret-123"

    # Never persisted: the skill's meta file must NOT contain the key.
    meta = module.read_skill_meta("netmind-vision")
    assert "NETMIND_API_KEY" not in (meta.get("env_config") or {})


@pytest.mark.asyncio
async def test_explicit_skill_config_wins_over_provider(db_client, workspace, tmp_path, monkeypatch):
    await _install_netmind_skill(db_client, tmp_path, monkeypatch)
    await db_client.insert(
        "user_providers",
        {"user_id": USER_ID, "source": "netmind", "protocol": "openai", "name": "NetMind",
         "provider_id": "netmind-openai", "api_key": "nm-provider-key",
         "base_url": "https://api.netmind.ai/inference-api/openai/v1"},
    )
    module = SkillModule(agent_id=AGENT_ID, user_id=USER_ID, database_client=db_client)
    module.set_skill_env_config("netmind-vision", {"NETMIND_API_KEY": "user-override"})

    skills = module.list_skills()
    env = await module._resolve_platform_env(module.get_all_skill_env_vars(), skills)
    assert env["NETMIND_API_KEY"] == "user-override"


@pytest.mark.asyncio
async def test_no_provider_no_injection(db_client, workspace, tmp_path, monkeypatch):
    await _install_netmind_skill(db_client, tmp_path, monkeypatch)
    module = SkillModule(agent_id=AGENT_ID, user_id=USER_ID, database_client=db_client)
    env = await module._resolve_platform_env(module.get_all_skill_env_vars(), module.list_skills())
    assert "NETMIND_API_KEY" not in env


@pytest.mark.asyncio
async def test_platform_var_counts_as_configured(db_client, workspace, tmp_path, monkeypatch):
    await _install_netmind_skill(db_client, tmp_path, monkeypatch)
    module = SkillModule(agent_id=AGENT_ID, user_id=USER_ID)
    skill = next(s for s in module.list_skills() if s.name == "netmind-vision")
    assert "NETMIND_API_KEY" in (skill.requires_env or [])
    assert skill.env_configured is True  # platform-resolved, no Needs Config badge


@pytest.mark.asyncio
async def test_declared_requires_suppresses_body_scan(db_client, workspace, tmp_path, monkeypatch):
    """Optional override vars mentioned in the SKILL.md body (NETMIND_BASE_URL
    etc.) must NOT be promoted to required when frontmatter declares
    requires.env explicitly."""
    registry = _registry(db_client, tmp_path)
    zip_path = tmp_path / "declared.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr(
            "declared-skill/SKILL.md",
            "---\nname: declared-skill\ndescription: d\nversion: 1.0.0\n"
            "metadata:\n  clawdbot:\n    requires:\n      env: [NETMIND_API_KEY]\n---\n"
            "Optionally set NETMIND_BASE_URL and NETMIND_VISION_MODEL to override.\n",
        )
        zf.writestr("declared-skill/manifest.json", json.dumps({"id": "declared-skill", "version": "1.0.0"}))
    await registry.publish(zip_path, "team")

    from xyz_agent_context._skill_marketplace_impl.install_pipeline import InstallPipeline

    pipeline = InstallPipeline(AGENT_ID, USER_ID, db_client=db_client)
    await pipeline.install_from_marketplace(
        "declared-skill", marketplace_source=LocalMarketplaceSource(registry)
    )
    skill = next(
        s for s in SkillModule(agent_id=AGENT_ID, user_id=USER_ID).list_skills()
        if s.name == "declared-skill"
    )
    assert skill.requires_env == ["NETMIND_API_KEY"]
    assert skill.env_configured is True


@pytest.mark.asyncio
async def test_platform_env_available_truthful(db_client, workspace):
    from xyz_agent_context.module.skill_module.skill_module import platform_env_available

    assert await platform_env_available(db_client, USER_ID) == set()
    await db_client.insert(
        "user_providers",
        {"user_id": USER_ID, "source": "netmind", "protocol": "openai", "name": "NetMind",
         "provider_id": "netmind-openai", "api_key": "nm-key",
         "base_url": "https://api.netmind.ai/inference-api/openai/v1"},
    )
    assert await platform_env_available(db_client, USER_ID) == {"NETMIND_API_KEY"}
    assert await platform_env_available(db_client, "someone_else") == set()
    assert await platform_env_available(None, USER_ID) == set()
