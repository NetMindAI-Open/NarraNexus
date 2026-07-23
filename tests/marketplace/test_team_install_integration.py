"""
@file_name: test_team_install_integration.py
@author: NetMind.AI
@date: 2026-07-21
@description: End-to-end Team Marketplace install against the REAL bundle
importer (not stubbed), using a real .nxbundle fixture.

Publishes the fixture into the local store + catalog, then runs
install_preflight — which resolves the blob, verifies sha256, and calls the
actual importer.preflight. Asserts a preflight token + a manifest describing
the agent(s) comes back. Skipped if the fixture blob is absent.
"""

from pathlib import Path

import pytest

from xyz_agent_context._skill_marketplace_impl.artifact_store import LocalArtifactStore

FIXTURE = Path(__file__).parent / "fixtures" / "pm-bridge-bot.nxbundle"
pytestmark = pytest.mark.skipif(
    not FIXTURE.exists(), reason="pm-bridge-bot.nxbundle fixture not present"
)


@pytest.fixture
def service(db_client, tmp_path, monkeypatch):
    from xyz_agent_context.settings import settings

    monkeypatch.setattr(settings, "base_working_path", str(tmp_path / "workspaces"))
    import xyz_agent_context.team_marketplace_service as mod

    monkeypatch.setattr(mod, "get_deployment_mode", lambda: "cloud")
    store = LocalArtifactStore(tmp_path / "team_store")
    return mod.TeamMarketplaceService(db_client=db_client, store=store)


@pytest.mark.asyncio
async def test_real_bundle_install_preflight(service):
    await service.publish(
        FIXTURE,
        template_id="pm-bridge-bot",
        name="PM Bridge Bot",
        description="single-agent bridge bot",
        categories=["productivity"],
        agent_count=1,
    )

    result = await service.install_preflight("pm-bridge-bot", "usr_integration")

    assert result.get("preflight_token")
    manifest = result.get("manifest") or {}
    # A valid bundle manifest describes at least one agent.
    agents = manifest.get("agents")
    assert agents, f"expected agents in manifest, got keys {list(manifest.keys())}"
    # Fresh user → no clashes on first install.
    assert result.get("name_clashes") == []
