"""
@file_name: test_freshness.py
@date: 2026-08-19
@description: TDD for external-edit detection (spec B §2).

Contract:
- refresh_external_state is the ONE detector: mtime fast-screen against the
  row's updated_at, sha256 verify against the row's content_hash.
    unchanged            → "fresh", commits nothing
    mtime moved, hash == → "fresh"  (touch/backup noise — no commit point)
    hash differs         → "external": row hash/updated_at refreshed,
                           history action="external_edited", staged
                           "updated" event with external=True
    entry gone           → "missing", commits nothing (heal's territory)
- office_lock_present detects the desktop-Office ~$ lock next to the entry.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from narranexus.platform.artifact import ArtifactService
from narranexus.platform.artifact._artifact_impl.freshness import office_lock_present
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.schema import Artifact
from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    (base / WS_REL).mkdir(parents=True)
    from narranexus.platform.settings import settings as sa
    monkeypatch.setattr(sa, "base_working_path", str(base), raising=False)

    entry = base / WS_REL / "report.md"
    entry.write_text("# v1\n", encoding="utf-8")

    repo = ArtifactRepository(db_client)
    # updated_at deliberately in the past so a fresh write's mtime is newer.
    past = datetime.now(timezone.utc) - timedelta(hours=1)
    art = Artifact(
        artifact_id="art_fresh001", agent_id="agent_x", user_id="user_y",
        session_id="s", title="report", kind="text/markdown",
        file_path=f"{WS_REL}/report.md", size_bytes=5,
        content_hash=_sha("# v1\n"), created_at=past, updated_at=past,
    )
    await repo.create(art)
    yield {
        "db": db_client, "repo": repo, "svc": ArtifactService(db_client),
        "art": art, "entry": entry, "base": base,
    }


async def _history(db, aid):
    rows = await db.execute(
        "SELECT action FROM instance_artifact_history WHERE artifact_id = %s",
        params=(aid,), fetch=True)
    return [r["action"] for r in rows]


async def _events(db):
    rows = await db.execute(
        "SELECT payload_json FROM instance_artifact_events WHERE agent_id = %s",
        params=("agent_x",), fetch=True)
    return [json.loads(r["payload_json"]) for r in rows]


async def test_unchanged_file_is_fresh_and_commits_nothing(env):
    art = await env["repo"].get_by_id("art_fresh001")
    verdict = await env["svc"].refresh_external_state(art)
    assert verdict == "fresh"
    assert await _history(env["db"], "art_fresh001") == []
    assert await _events(env["db"]) == []


async def test_touch_without_content_change_is_fresh(env):
    # bump mtime, keep bytes — backup tools / `touch` noise
    future = datetime.now(timezone.utc).timestamp() + 60
    os.utime(env["entry"], (future, future))
    art = await env["repo"].get_by_id("art_fresh001")
    verdict = await env["svc"].refresh_external_state(art)
    assert verdict == "fresh"
    assert await _history(env["db"], "art_fresh001") == []
    assert await _events(env["db"]) == []


async def test_content_change_commits_external_edit(env):
    env["entry"].write_text("# v2 external\n", encoding="utf-8")
    art = await env["repo"].get_by_id("art_fresh001")
    verdict = await env["svc"].refresh_external_state(art)
    assert verdict == "external"

    row = await env["repo"].get_by_id("art_fresh001")
    assert row.content_hash == _sha("# v2 external\n")
    assert row.updated_at > env["art"].updated_at
    assert await _history(env["db"], "art_fresh001") == ["external_edited"]
    events = await _events(env["db"])
    assert len(events) == 1
    assert events[0]["action"] == "updated"
    assert events[0]["external"] is True


async def test_missing_entry_reports_missing_without_commits(env):
    env["entry"].unlink()
    art = await env["repo"].get_by_id("art_fresh001")
    verdict = await env["svc"].refresh_external_state(art)
    assert verdict == "missing"
    assert await _history(env["db"], "art_fresh001") == []


def test_office_lock_present(tmp_path):
    doc = tmp_path / "Q3 deck.pptx"
    doc.write_bytes(b"pk")
    assert office_lock_present(str(doc)) is False
    (tmp_path / "~$Q3 deck.pptx").write_bytes(b"lock")
    assert office_lock_present(str(doc)) is True


async def test_null_hash_row_claims_fingerprint_without_commit(env):
    """Legacy rows (column added 2026-08-19) have content_hash NULL. A moved
    mtime on them must NOT be declared an external edit — there is no
    baseline to compare against. First sight claims the fingerprint
    (hash written back, updated_at bumped) with NO history row, NO event."""
    await env["db"].update(
        "instance_artifacts", {"artifact_id": "art_fresh001"}, {"content_hash": None}
    )
    art = await env["repo"].get_by_id("art_fresh001")
    assert art.content_hash is None

    verdict = await env["svc"].refresh_external_state(art)
    assert verdict == "fresh"
    row = await env["repo"].get_by_id("art_fresh001")
    assert row.content_hash == _sha("# v1\n")  # fingerprint claimed
    assert await _history(env["db"], "art_fresh001") == []
    assert await _events(env["db"]) == []


async def test_null_hash_row_with_changed_bytes_is_still_fresh(env):
    """Even when the bytes differ from what was once registered, a NULL-hash
    row cannot support the claim 'externally edited' — we never knew the
    old content. Claim, don't accuse."""
    await env["db"].update(
        "instance_artifacts", {"artifact_id": "art_fresh001"}, {"content_hash": None}
    )
    env["entry"].write_text("# rewritten while unfingerprinted\n", encoding="utf-8")
    art = await env["repo"].get_by_id("art_fresh001")

    verdict = await env["svc"].refresh_external_state(art)
    assert verdict == "fresh"
    row = await env["repo"].get_by_id("art_fresh001")
    assert row.content_hash == _sha("# rewritten while unfingerprinted\n")
    assert await _history(env["db"], "art_fresh001") == []
    assert await _events(env["db"]) == []
