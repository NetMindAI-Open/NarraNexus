"""
@file_name: test_notify.py
@date: 2026-08-18
@description: TDD for the artifact_changed staging chokepoint (notify.py).

Contract under test (spec 2026-08-18-artifact-events-inventory-pointer §3):
- stage_artifact_event writes ONE self-contained outbox row; the payload
  carries the FULL artifact row including file_path — the HTTP list routes
  already return it to the authenticated owner, and excluding it here would
  give the frontend store two Artifact shapes (HtmlRenderer branches on
  file_path), which is fragility without secrecy.
- Staging is best-effort: a broken DB must not raise into the caller.
- Registry write paths stage exactly one row each: register → "registered",
  target re-register → "updated", service bulk delete → "deleted" per row.
"""
from __future__ import annotations

import json

import pytest

from narranexus.platform.artifact import ArtifactService
from narranexus.platform.artifact._artifact_impl.notify import stage_artifact_event
from narranexus.platform.repository.artifact_repository import ArtifactRepository
from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

WS_REL = agent_workspace_relpath("agent_x", "user_y")


@pytest.fixture
async def env(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    base.mkdir()
    from narranexus.platform.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(base), raising=False)

    workspace = base / WS_REL
    workspace.mkdir(parents=True)
    (workspace / "report").mkdir()
    entry = workspace / "report" / "index.html"
    entry.write_text("<p>hi</p>", encoding="utf-8")

    yield {
        "db": db_client,
        "repo": ArtifactRepository(db_client),
        "entry": entry,
    }


async def _outbox_rows(db, agent_id: str = "agent_x"):
    return await db.execute(
        "SELECT payload_json, consumed_at FROM instance_artifact_events "
        "WHERE agent_id = %s ORDER BY id",
        params=(agent_id,),
        fetch=True,
    )


async def _register(env, target_artifact_id=None):
    return await ArtifactService(env["db"]).register(
        agent_id="agent_x", user_id="user_y", session_id="sess_1",
        kind="text/html", entry_path=str(env["entry"]),
        title="My report", description=None,
        target_artifact_id=target_artifact_id,
    )


@pytest.mark.asyncio
async def test_stage_writes_self_contained_full_row_payload(env):
    result = await _register(env)
    artifact = await env["repo"].get_by_id(result.artifact_id)

    await stage_artifact_event(env["db"], action="repointed", artifact=artifact,
                               extra={"old": "report/a.html", "new": "report/b.html"})

    rows = await _outbox_rows(env["db"])
    payload = json.loads(rows[-1]["payload_json"])
    assert payload["type"] == "artifact_changed"
    assert payload["action"] == "repointed"
    assert payload["external"] is False
    assert payload["artifact"]["artifact_id"] == result.artifact_id
    assert payload["artifact"]["title"] == "My report"
    assert payload["artifact"]["file_path"] == artifact.file_path
    assert payload["extra"]["new"] == "report/b.html"
    assert rows[-1]["consumed_at"] is None


@pytest.mark.asyncio
async def test_stage_never_raises_when_db_is_broken(env):
    artifact = await env["repo"].get_by_id(
        (await _register(env)).artifact_id
    )

    class BrokenDb:
        async def insert(self, *args, **kwargs):
            raise RuntimeError("db down")

    # Must swallow (log-only) — the accompanying write path owns success.
    await stage_artifact_event(BrokenDb(), action="registered", artifact=artifact)


@pytest.mark.asyncio
async def test_register_stages_registered_event(env):
    await _register(env)
    actions = [json.loads(r["payload_json"])["action"]
               for r in await _outbox_rows(env["db"])]
    assert actions == ["registered"]


@pytest.mark.asyncio
async def test_reregister_stages_updated_event(env):
    result = await _register(env)
    await _register(env, target_artifact_id=result.artifact_id)
    actions = [json.loads(r["payload_json"])["action"]
               for r in await _outbox_rows(env["db"])]
    assert actions == ["registered", "updated"]


@pytest.mark.asyncio
async def test_service_bulk_delete_stages_deleted_per_row(env):
    r1 = await _register(env)
    svc = ArtifactService(env["db"])
    deleted, skipped = await svc.bulk_delete(
        user_id="user_y", artifact_ids=[r1.artifact_id, "art_nothere"]
    )
    assert deleted == 1
    assert skipped == ["art_nothere"]

    rows = await _outbox_rows(env["db"])
    actions = [json.loads(r["payload_json"])["action"] for r in rows]
    assert actions == ["registered", "deleted"]
    deleted_payload = json.loads(rows[-1]["payload_json"])
    assert deleted_payload["artifact"]["artifact_id"] == r1.artifact_id
