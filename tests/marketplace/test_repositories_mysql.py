"""
@file_name: test_repositories_mysql.py
@author: NetMind.AI
@date: 2026-07-22
@description: MySQL dual-dialect coverage for the marketplace repositories'
hand-written SQL.

The marketplace catalog repos issue raw SQL (SkillCatalogRepository.search /
list_defaults / increment_downloads, SkillScanResultRepository.latest_for,
TeamCatalogRepository.list_enabled / list_all / increment_downloads). The rest
of tests/marketplace runs on SQLite; this file exercises the same statements
against a REAL MySQL so a dialect divergence (identifier quoting, LIMIT/OFFSET,
`%s` vs `?`, reserved words, JSON LIKE) is caught by machine, not by reading.

Enable by pointing NARRANEXUS_MYSQL_TEST_URL at a throwaway MySQL, e.g.:

    export NARRANEXUS_MYSQL_TEST_URL=\\
        "mysql://root:xyz_root_pass@127.0.0.1:3306/xyz_agent_context"
"""
from __future__ import annotations

import os

import pytest
import pytest_asyncio

from xyz_agent_context.repository.skill_catalog_repository import SkillCatalogRepository
from xyz_agent_context.repository.skill_scan_result_repository import (
    SkillScanResultRepository,
)
from xyz_agent_context.repository.team_catalog_repository import TeamCatalogRepository
from xyz_agent_context.schema.skill_marketplace_schema import (
    SkillCatalogEntry,
    SkillScanResult,
)
from xyz_agent_context.schema.team_marketplace_schema import TeamTemplate
from xyz_agent_context.utils.database import AsyncDatabaseClient
from xyz_agent_context.utils.db_backend_mysql import MySQLBackend
from xyz_agent_context.utils.schema_registry import auto_migrate

MYSQL_URL_ENV = "NARRANEXUS_MYSQL_TEST_URL"

pytestmark = pytest.mark.skipif(
    not os.environ.get(MYSQL_URL_ENV),
    reason=f"{MYSQL_URL_ENV} not set — dual-dialect tests need a real MySQL",
)


def _parse_mysql_url(url: str) -> dict:
    assert url.startswith("mysql://"), f"expected mysql://..., got {url!r}"
    creds, _, host_db = url[len("mysql://"):].partition("@")
    user, _, password = creds.partition(":")
    host_port, _, database = host_db.partition("/")
    host, _, port = host_port.partition(":")
    return {"host": host, "port": int(port) if port else 3306,
            "user": user, "password": password, "database": database}


@pytest_asyncio.fixture
async def mysql_db():
    cfg = _parse_mysql_url(os.environ[MYSQL_URL_ENV])
    backend = MySQLBackend(cfg)
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    # Idempotent: clear the rows these tests touch (shared DB).
    for tbl, col, vals in (
        ("skill_catalog", "skill_id", ("mysql-web", "mysql-vision")),
        ("skill_scan_results", "skill_id", ("mysql-web",)),
        ("team_catalog", "template_id", ("mysql-team",)),
    ):
        for v in vals:
            await client.execute(f"DELETE FROM {tbl} WHERE {col} = %s", params=(v,), fetch=False)
    yield client
    await client.close()


def _skill(skill_id, version, **over):
    payload = dict(
        skill_id=skill_id, version=version, name=skill_id.title(),
        description="d", category="utility", capabilities=["search:web"],
        tags=["search", "web"], s3_key=f"{skill_id}/{version}/x.zip",
        package_hash=f"sha256:{skill_id}-{version}", scan_status="passed",
        status="published", published_at="2026-07-22 00:00:00",
    )
    payload.update(over)
    return SkillCatalogEntry(**payload)


@pytest.mark.asyncio
async def test_skill_catalog_raw_sql_on_mysql(mysql_db):
    repo = SkillCatalogRepository(mysql_db)
    await repo.publish(_skill("mysql-web", "1.0.0"))
    await repo.publish(_skill("mysql-web", "1.2.0"))
    await repo.publish(_skill("mysql-vision", "1.0.0", category="fallback",
                              capabilities=["vision:image"], tags=["vision"],
                              is_default=True))

    # search(): WHERE ... LIKE %s, JSON substring, latest-per-skill dedup
    items, total = await repo.search(q="mysql")
    ids = sorted(i.skill_id for i in items)
    assert ids == ["mysql-vision", "mysql-web"] and total == 2
    web = next(i for i in items if i.skill_id == "mysql-web")
    assert web.version == "1.2.0"  # latest only

    cap, _ = await repo.search(capability="vision:image")
    assert [i.skill_id for i in cap] == ["mysql-vision"]

    # list_defaults()
    defaults = await repo.list_defaults()
    assert [d.skill_id for d in defaults] == ["mysql-vision"]

    # increment_downloads() (raw UPDATE ... + 1)
    await repo.increment_downloads("mysql-web", "1.0.0")
    await repo.increment_downloads("mysql-web", "1.0.0")
    assert (await repo.get_version("mysql-web", "1.0.0")).downloads == 2


@pytest.mark.asyncio
async def test_scan_result_latest_for_on_mysql(mysql_db):
    repo = SkillScanResultRepository(mysql_db)
    await repo.record(SkillScanResult(skill_id="mysql-web", version="1.0.0",
                                      status="warning", low_issues=1, scanner_version="1.0.0"))
    await repo.record(SkillScanResult(skill_id="mysql-web", version="1.0.0",
                                      status="passed", scanner_version="1.1.0"))
    latest = await repo.latest_for("mysql-web", "1.0.0")  # ORDER BY id DESC LIMIT 1
    assert latest.status == "passed" and latest.scanner_version == "1.1.0"


@pytest.mark.asyncio
async def test_team_catalog_raw_sql_on_mysql(mysql_db):
    repo = TeamCatalogRepository(mysql_db)
    await repo.save_template(TeamTemplate(
        template_id="mysql-team", name="MySQL Team", categories=["team"],
        agent_count=3, store_key="mysql-team/x/t.nxbundle", bundle_sha256="deadbeef",
        sort_order=1))
    enabled = await repo.list_enabled()   # WHERE enabled = 1 ORDER BY ...
    assert any(t.template_id == "mysql-team" for t in enabled)
    assert any(t.template_id == "mysql-team" for t in await repo.list_all())
    await repo.increment_downloads("mysql-team")
    assert (await repo.get("mysql-team")).downloads == 1
