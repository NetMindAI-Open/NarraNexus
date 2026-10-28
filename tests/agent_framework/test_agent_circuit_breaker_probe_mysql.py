"""
@file_name: test_agent_circuit_breaker_probe_mysql.py
@author:
@date: 2026-09-10
@description: The half-open probe claim's compare-and-swap, on the dialect
where a naive claim was measured to let every racer through.

``AgentCircuitBreakerRepository.try_claim_probe`` is an equality-filtered
``UPDATE`` keyed on ``probe_token``; "did I win" is ``rowcount > 0``. Two
dialect-sensitive facts make a real-MySQL twin necessary rather than nice:

  * aiomysql reports CHANGED rows, not MATCHED rows. The reclaim-from-PROBING
    branch writes ``cb_status='probing'`` over ``cb_status='probing'``, so on
    MySQL the only guaranteed-changed columns are ``probe_token`` (always a
    fresh value) and ``cooldown_until``. A future "just update cb_status"
    simplification would pass on SQLite and silently never win on MySQL.
  * The first claim after a pause filters on ``probe_token IS NULL``. The
    backends translate a ``None`` filter to ``IS NULL``; a regression to
    ``= NULL`` would match nothing and disable half-open entirely — again
    invisible on a fixture that fakes the dialect.

Run locally the same way as the other twins:

    docker run --rm -d -p 3307:3306 -e MYSQL_ROOT_PASSWORD=root \\
        -e MYSQL_DATABASE=nxtest --name nx-mysql-test mysql:8
    export NARRANEXUS_MYSQL_TEST_URL=mysql://root:root@127.0.0.1:3307/nxtest
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest
import pytest_asyncio

from narranexus.platform.agent_framework.loop.circuit_breaker import (
    AUTH_QUOTA_PAUSE_THRESHOLD,
    bind_probe_run,
    release_orphaned_probe,
    release_probe,
    settle_probe,
    try_begin_probe,
)
from narranexus.platform.repository.agent_circuit_breaker_repository import (
    AgentCircuitBreakerRepository,
)
from narranexus.platform.schema import CbStatus, ErrorCategory, PausedReason
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate
from narranexus.platform.utils.timezone import utc_now

from tests.mysql_dialect import (
    mysql_configured,
    mysql_url,
    parse_mysql_url,
    skip_reason,
)

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "the probe_token compare-and-swap under aiomysql's CHANGED-rows "
        "rowcount and its IS NULL first-claim filter"
    ),
)

_PREFIX = "mysqlprobe"
TABLE = AgentCircuitBreakerRepository.table_name


async def _clean(client, *, quiet: bool) -> None:
    try:
        await client.execute(
            f"DELETE FROM {TABLE} WHERE agent_id LIKE %s", (f"%{_PREFIX}%",), fetch=False,
        )
        await client.execute(
            "DELETE FROM events WHERE agent_id LIKE %s", (f"%{_PREFIX}%",), fetch=False,
        )
    except Exception:  # noqa: BLE001 — see the convention in the rename twin
        if not quiet:
            raise


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)
    await _clean(client, quiet=False)
    yield client
    await _clean(client, quiet=True)
    await client.close()


async def _claim(aid: str, db) -> tuple:
    admission = await try_begin_probe(aid, db=db)
    return (admission.allowed, admission.reason)


def _paused(**overrides) -> dict:
    row = {
        "cb_status": CbStatus.PAUSED.value,
        "paused_reason": PausedReason.AUTH.value,
        "failure_category": ErrorCategory.AUTH.value,
        "consecutive_failure_count": AUTH_QUOTA_PAUSE_THRESHOLD,
        "cooldown_until": utc_now() - timedelta(seconds=1),
        "probe_token": None,
    }
    row.update(overrides)
    return row


@pytest.mark.asyncio
async def test_first_claim_matches_a_null_probe_token(mysql_client):
    """A fresh pause has probe_token NULL; the claim must still match it
    (IS NULL, not = NULL) or half-open never opens on cloud."""
    repo = AgentCircuitBreakerRepository(mysql_client)
    aid = f"agent_{_PREFIX}_null"
    await repo.upsert_state(aid, _paused())
    assert await _claim(aid, mysql_client) == (True, None)
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.PROBING.value
    assert row.probe_token


@pytest.mark.asyncio
async def test_concurrent_first_claims_have_one_winner(mysql_client):
    repo = AgentCircuitBreakerRepository(mysql_client)
    aid = f"agent_{_PREFIX}_race"
    await repo.upsert_state(aid, _paused())
    results = await asyncio.gather(*[_claim(aid, mysql_client) for _ in range(4)])
    assert results.count((True, None)) == 1
    assert results.count((False, "probing")) == 3


@pytest.mark.asyncio
async def test_concurrent_reclaims_of_stale_probing_have_one_winner(mysql_client):
    """The branch a status-only CAS could not guard: probing → probing."""
    repo = AgentCircuitBreakerRepository(mysql_client)
    aid = f"agent_{_PREFIX}_stale"
    await repo.upsert_state(aid, _paused(cb_status=CbStatus.PROBING.value, probe_token="old"))
    results = await asyncio.gather(*[_claim(aid, mysql_client) for _ in range(4)])
    assert results.count((True, None)) == 1
    row = await repo.get(aid)
    assert row.probe_token != "old"


@pytest.mark.asyncio
async def test_claim_with_a_stale_expected_token_loses(mysql_client):
    """The repository contract itself: a caller whose read is out of date
    (someone re-claimed since) gets None — never a spurious win."""
    repo = AgentCircuitBreakerRepository(mysql_client)
    aid = f"agent_{_PREFIX}_stalecas"
    await repo.upsert_state(aid, _paused(cb_status=CbStatus.PROBING.value, probe_token="current"))
    grant = utc_now() + timedelta(minutes=5)
    assert await repo.try_claim_probe(aid, CbStatus.PROBING.value, "older-read", grant) is None
    won = await repo.try_claim_probe(aid, CbStatus.PROBING.value, "current", grant)
    assert won and (await repo.get(aid)).probe_token == won


@pytest.mark.asyncio
async def test_settlement_cas_wins_only_with_the_live_token(mysql_client):
    """settle_probe / release_probe write through the probe_token CAS. On
    MySQL the rowcount is CHANGED rows — the win must still register (the
    token column always changes), and a wrong token must lose."""
    repo = AgentCircuitBreakerRepository(mysql_client)
    aid = f"agent_{_PREFIX}_settle"
    await repo.upsert_state(aid, _paused())
    adm = await try_begin_probe(aid, db=mysql_client)
    assert adm.probe_token
    assert await release_probe(aid, "not-the-token", db=mysql_client) is False
    assert (await repo.get(aid)).cb_status == CbStatus.PROBING.value
    await settle_probe(aid, adm.probe_token, succeeded=True, db=mysql_client)
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.ACTIVE.value
    assert row.probe_token is None and row.probe_run_id is None


@pytest.mark.asyncio
async def test_claimant_identity_binds_and_is_judged_on_mysql(mysql_client):
    """The claimant names its run through a token CAS that leaves the row
    PROBING (rowcount = CHANGED rows on aiomysql: the bind must still
    register), and liveness is then judged on THAT run only — the
    heartbeat comes back as a naive DATETIME(6) here. A newer, unrelated
    live run of the agent does not keep the claim alive."""
    repo = AgentCircuitBreakerRepository(mysql_client)
    aid = f"agent_{_PREFIX}_live"
    await repo.upsert_state(aid, _paused())
    adm = await try_begin_probe(aid, db=mysql_client)
    assert adm.probe_token
    claimant = f"evt_{_PREFIX}_claimant"
    await mysql_client.insert("events", {
        "event_id": claimant, "agent_id": aid, "user_id": "u",
        "trigger": "chat", "trigger_source": "websocket", "state": "running",
        "started_at": utc_now(), "last_event_at": utc_now(),
    })
    assert await bind_probe_run(aid, "not-the-token", claimant, db=mysql_client) is False
    assert await bind_probe_run(aid, adm.probe_token, claimant, db=mysql_client) is True
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.PROBING.value and row.probe_run_id == claimant

    await mysql_client.insert("events", {
        "event_id": f"evt_{_PREFIX}_newer", "agent_id": aid, "user_id": "u",
        "trigger": "lark", "trigger_source": "lark", "state": "running",
        "started_at": utc_now(), "last_event_at": utc_now(),
    })
    assert await release_orphaned_probe(aid, db=mysql_client) is False
    await mysql_client.update(
        "events", {"event_id": claimant},
        {"last_event_at": utc_now() - timedelta(hours=1)},
    )
    assert await release_orphaned_probe(aid, db=mysql_client) is True
    row = await repo.get(aid)
    assert row.cb_status == CbStatus.PAUSED.value and row.probe_run_id is None
