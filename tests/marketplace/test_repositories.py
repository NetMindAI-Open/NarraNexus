"""
@file_name: test_repositories.py
@author: NetMind.AI
@date: 2026-07-20
@description: Repository tests for the Skill Marketplace tables.

Covers SkillCatalogRepository (publish/upsert, semver-aware latest, search
with filters + pagination, download counter), SkillInstallationRepository
(triple-key upsert, status transitions, workspace listing), and
SkillScanResultRepository (append + latest wins).
"""

import pytest

from xyz_agent_context.repository.skill_catalog_repository import SkillCatalogRepository
from xyz_agent_context.repository.skill_installation_repository import (
    SkillInstallationRepository,
)
from xyz_agent_context.repository.skill_scan_result_repository import (
    SkillScanResultRepository,
)
from xyz_agent_context.schema.skill_marketplace_schema import (
    SkillCatalogEntry,
    SkillScanResult,
)


def _entry(skill_id: str, version: str, **overrides) -> SkillCatalogEntry:
    payload = {
        "skill_id": skill_id,
        "version": version,
        "name": skill_id.replace("-", " ").title(),
        "description": f"{skill_id} does things",
        "category": "utility",
        "capabilities": ["search:web"],
        "tags": ["search", "web"],
        "s3_key": f"narranexus-skills/{skill_id}/{version}/{skill_id}-{version}.zip",
        "package_hash": f"sha256:{skill_id}-{version}",
        "publisher": "narranexus-team",
        "scan_status": "passed",
        "status": "published",
        "published_at": "2026-07-20 10:00:00",
    }
    payload.update(overrides)
    return SkillCatalogEntry(**payload)


# ---------------------------------------------------------------------------
# SkillCatalogRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_catalog_publish_and_get_version(db_client):
    repo = SkillCatalogRepository(db_client)
    await repo.publish(_entry("web-search-fallback", "1.0.0"))

    got = await repo.get_version("web-search-fallback", "1.0.0")
    assert got is not None
    assert got.name == "Web Search Fallback"
    assert got.capabilities == ["search:web"]
    assert got.tags == ["search", "web"]


@pytest.mark.asyncio
async def test_catalog_publish_same_version_is_upsert(db_client):
    repo = SkillCatalogRepository(db_client)
    await repo.publish(_entry("web-search-fallback", "1.0.0"))
    await repo.publish(_entry("web-search-fallback", "1.0.0", description="updated"))

    versions = await repo.list_versions("web-search-fallback")
    assert len(versions) == 1
    assert versions[0].description == "updated"


@pytest.mark.asyncio
async def test_catalog_get_latest_uses_semver_not_string_order(db_client):
    repo = SkillCatalogRepository(db_client)
    await repo.publish(_entry("web-search-fallback", "1.9.0"))
    await repo.publish(_entry("web-search-fallback", "1.10.0"))

    latest = await repo.get_latest("web-search-fallback")
    assert latest is not None
    assert latest.version == "1.10.0"


@pytest.mark.asyncio
async def test_catalog_get_latest_ignores_deprecated(db_client):
    repo = SkillCatalogRepository(db_client)
    await repo.publish(_entry("web-search-fallback", "1.0.0"))
    await repo.publish(_entry("web-search-fallback", "2.0.0", status="deprecated"))

    latest = await repo.get_latest("web-search-fallback")
    assert latest is not None
    assert latest.version == "1.0.0"


@pytest.mark.asyncio
async def test_catalog_search_by_keyword_and_filters(db_client):
    repo = SkillCatalogRepository(db_client)
    await repo.publish(_entry("web-search-fallback", "1.0.0"))
    await repo.publish(
        _entry(
            "multimodal-fallback",
            "1.0.0",
            category="fallback",
            capabilities=["vision:image"],
            tags=["vision"],
            description="image understanding fallback",
        )
    )

    items, total = await repo.search(q="search")
    assert total == 1
    assert items[0].skill_id == "web-search-fallback"

    items, total = await repo.search(category="fallback")
    assert total == 1
    assert items[0].skill_id == "multimodal-fallback"

    items, total = await repo.search(capability="vision:image")
    assert total == 1
    assert items[0].skill_id == "multimodal-fallback"

    items, total = await repo.search()
    assert total == 2


@pytest.mark.asyncio
async def test_catalog_search_returns_only_latest_version_per_skill(db_client):
    repo = SkillCatalogRepository(db_client)
    await repo.publish(_entry("web-search-fallback", "1.0.0"))
    await repo.publish(_entry("web-search-fallback", "1.2.0"))

    items, total = await repo.search()
    assert total == 1
    assert items[0].version == "1.2.0"


@pytest.mark.asyncio
async def test_catalog_search_pagination(db_client):
    repo = SkillCatalogRepository(db_client)
    for i in range(5):
        await repo.publish(_entry(f"skill-{i}", "1.0.0"))

    items, total = await repo.search(sort="name", page=2, limit=2)
    assert total == 5
    assert [s.skill_id for s in items] == ["skill-2", "skill-3"]


@pytest.mark.asyncio
async def test_catalog_increment_downloads(db_client):
    repo = SkillCatalogRepository(db_client)
    await repo.publish(_entry("web-search-fallback", "1.0.0"))

    await repo.increment_downloads("web-search-fallback", "1.0.0")
    await repo.increment_downloads("web-search-fallback", "1.0.0")

    got = await repo.get_version("web-search-fallback", "1.0.0")
    assert got.downloads == 2


# ---------------------------------------------------------------------------
# SkillInstallationRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_installation_upsert_is_unique_per_workspace_skill(db_client):
    repo = SkillInstallationRepository(db_client)
    await repo.upsert_event(
        agent_id="agt_1",
        user_id="usr_1",
        skill_id="web-search-fallback",
        version="1.0.0",
        source_type="marketplace",
        package_hash="sha256:aaa",
    )
    await repo.upsert_event(
        agent_id="agt_1",
        user_id="usr_1",
        skill_id="web-search-fallback",
        version="1.2.0",
        source_type="marketplace",
        package_hash="sha256:bbb",
        last_event="update",
    )

    rows = await repo.list_for_workspace("agt_1", "usr_1")
    assert len(rows) == 1
    assert rows[0].version == "1.2.0"
    assert rows[0].last_event == "update"
    assert rows[0].status == "installed"


@pytest.mark.asyncio
async def test_installation_separate_workspaces_do_not_collide(db_client):
    repo = SkillInstallationRepository(db_client)
    for agent in ("agt_1", "agt_2"):
        await repo.upsert_event(
            agent_id=agent,
            user_id="usr_1",
            skill_id="web-search-fallback",
            version="1.0.0",
            source_type="url",
            source_url="https://example.com/skill.zip",
        )

    assert len(await repo.list_for_workspace("agt_1", "usr_1")) == 1
    assert len(await repo.list_for_workspace("agt_2", "usr_1")) == 1


@pytest.mark.asyncio
async def test_installation_mark_status_reconcile(db_client):
    repo = SkillInstallationRepository(db_client)
    await repo.upsert_event(
        agent_id="agt_1",
        user_id="usr_1",
        skill_id="web-search-fallback",
        version="1.0.0",
        source_type="zip",
    )

    changed = await repo.mark_status(
        "agt_1", "usr_1", "web-search-fallback", status="external_removed"
    )
    assert changed is True

    rows = await repo.list_for_workspace("agt_1", "usr_1")
    assert rows[0].status == "external_removed"
    assert rows[0].last_event == "reconcile"


@pytest.mark.asyncio
async def test_installation_mark_status_missing_row_returns_false(db_client):
    repo = SkillInstallationRepository(db_client)
    changed = await repo.mark_status("agt_x", "usr_x", "nope", status="modified")
    assert changed is False


# ---------------------------------------------------------------------------
# SkillScanResultRepository
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scan_results_append_and_latest_wins(db_client):
    repo = SkillScanResultRepository(db_client)
    await repo.record(
        SkillScanResult(
            skill_id="web-search-fallback",
            version="1.0.0",
            status="warning",
            high_issues=0,
            low_issues=2,
            issues=[{"rule": "subprocess", "severity": "low"}],
            scanner_version="1.0.0",
        )
    )
    await repo.record(
        SkillScanResult(
            skill_id="web-search-fallback",
            version="1.0.0",
            status="passed",
            high_issues=0,
            low_issues=0,
            issues=[],
            scanner_version="1.1.0",
        )
    )

    latest = await repo.latest_for("web-search-fallback", "1.0.0")
    assert latest is not None
    assert latest.status == "passed"
    assert latest.scanner_version == "1.1.0"


@pytest.mark.asyncio
async def test_scan_results_latest_for_missing_returns_none(db_client):
    repo = SkillScanResultRepository(db_client)
    assert await repo.latest_for("nope", "1.0.0") is None
