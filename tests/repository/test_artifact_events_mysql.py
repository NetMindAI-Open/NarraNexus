"""
@file_name: test_artifact_events_mysql.py
@author: NetMind.AI
@date: 2026-08-20
@description: Real-MySQL dialect twin for the artifact-editing SQL surface
(review #334 r2 C2).

This exists to un-hide the exact false green that shipped C1: ``ESCAPE '\\'``
is legal on SQLite (whose string literals do not process backslashes) and a
1064 syntax error on MySQL (whose literals do) — so the 6997-test SQLite
suite proved nothing about the only dialect dev/prod run. Everything here is
hand-written SQL added by PR #334:

- ArtifactEventRepository: stage → pending_for_agent → mark_consumed, with
  the variable-length IN-list expanded at 1 id and at 5 ids;
- search_agent_context / count_agent_context_filtered with a title needle
  containing ``%``, ``_`` and ``!`` — asserting LITERAL matching (the LIKE
  metacharacters must not wildcard) and that the statement parses at all;
- latest_actions with 20 artifact ids (double IN-list + MAX(id) subquery).

Enable with NARRANEXUS_MYSQL_TEST_URL (same convention as the other
*_mysql.py twins); skipped otherwise.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
import pytest_asyncio

from narranexus.platform.repository.artifact_event_repository import (
    ArtifactEventRepository,
)
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.repository.team_workspace_repository import (
    ArtifactHistoryRepository,
)
from narranexus.platform.schema.artifact_schema import Artifact
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.db_backend_mysql import MySQLBackend
from narranexus.platform.utils.db.schema_registry import auto_migrate
from tests.mysql_dialect import mysql_configured, mysql_url, parse_mysql_url, skip_reason

pytestmark = pytest.mark.skipif(
    not mysql_configured(),
    reason=skip_reason(
        "that the artifact outbox SQL, the LIKE-escaped context search and "
        "the MAX(id) latest_actions subquery all parse and behave on the "
        "real MySQL dialect"
    ),
)

AGENT = "agent_mysql_twin"


@pytest_asyncio.fixture
async def mysql_client():
    backend = MySQLBackend(parse_mysql_url(mysql_url()))
    await backend.initialize()
    await auto_migrate(backend)
    client = await AsyncDatabaseClient.create_with_backend(backend)

    async def _cleanup():
        await client.delete("instance_artifact_events", {"agent_id": AGENT})
        await client.execute(
            "DELETE FROM instance_artifact_history WHERE agent_id = %s",
            params=(AGENT,),
        )
        await client.execute(
            "DELETE FROM instance_artifacts WHERE agent_id = %s", params=(AGENT,)
        )

    await _cleanup()
    yield client
    await _cleanup()
    await client.close()


def _artifact(aid: str, title: str) -> Artifact:
    now = datetime.now(timezone.utc)
    return Artifact(
        artifact_id=aid, agent_id=AGENT, user_id="user_twin",
        session_id=None, title=title, kind="text/markdown", pinned=True,
        file_path=f"{AGENT}_user_twin/{aid}.md", size_bytes=1,
        created_at=now, updated_at=now,
    )


@pytest.mark.asyncio
async def test_outbox_stage_pending_consume_roundtrip(mysql_client):
    repo = ArtifactEventRepository(mysql_client)
    for i in range(6):
        await repo.stage(
            agent_id=AGENT, payload_json=json.dumps({"n": i}, ensure_ascii=False)
        )
    rows = await repo.pending_for_agent(AGENT, limit=10)
    assert [json.loads(r["payload_json"])["n"] for r in rows] == [0, 1, 2, 3, 4, 5]

    # IN-list expansion at 1 id and at 5 ids — the variable-placeholder shape
    await repo.mark_consumed([rows[0]["id"]])
    remaining = await repo.pending_for_agent(AGENT, limit=10)
    assert len(remaining) == 5
    await repo.mark_consumed([r["id"] for r in remaining])
    assert await repo.pending_for_agent(AGENT, limit=10) == []


@pytest.mark.asyncio
async def test_context_search_like_escape_parses_and_matches_literally(mysql_client):
    repo = ArtifactRepository(mysql_client)
    await repo.create(_artifact("art_mtwin_01", "sales 100% report"))
    await repo.create(_artifact("art_mtwin_02", "sales 100x report"))
    await repo.create(_artifact("art_mtwin_03", "a_b literal"))
    await repo.create(_artifact("art_mtwin_04", "axb not literal"))
    await repo.create(_artifact("art_mtwin_05", "bang! title"))
    await repo.create(_artifact("art_mtwin_06", "back\\slash"))
    await repo.create(_artifact("art_mtwin_07", "backxslash"))

    # '%' must match literally, not wildcard
    hits = await repo.search_agent_context(AGENT, title_contains="100%")
    assert [a.artifact_id for a in hits] == ["art_mtwin_01"]
    assert await repo.count_agent_context_filtered(AGENT, title_contains="100%") == 1

    # '_' must match literally, not any-char
    hits = await repo.search_agent_context(AGENT, title_contains="a_b")
    assert [a.artifact_id for a in hits] == ["art_mtwin_03"]

    # the escape char itself must round-trip
    hits = await repo.search_agent_context(AGENT, title_contains="bang!")
    assert [a.artifact_id for a in hits] == ["art_mtwin_05"]

    # backslash: the char whose ESCAPE role was REMOVED this round — it must
    # now match literally through both dialects' literal/param layers
    hits = await repo.search_agent_context(AGENT, title_contains="back\\slash")
    assert [a.artifact_id for a in hits] == ["art_mtwin_06"]


@pytest.mark.asyncio
async def test_latest_actions_double_inlist_at_twenty_ids(mysql_client):
    repo = ArtifactRepository(mysql_client)
    hist = ArtifactHistoryRepository(mysql_client)
    ids = [f"art_mtwin_h{i:02d}" for i in range(20)]
    for aid in ids:
        await repo.create(_artifact(aid, aid))
        await hist.append(
            artifact_id=aid, agent_id=AGENT, file_path="x", size_bytes=1,
            action="created",
        )
    # newest action wins for one of them
    await hist.append(
        artifact_id=ids[7], agent_id=AGENT, file_path="x", size_bytes=1,
        action="user_edited",
    )
    latest = await hist.latest_actions(ids)
    assert latest[ids[7]] == "user_edited"
    assert latest[ids[0]] == "created"
    assert len(latest) == 20
