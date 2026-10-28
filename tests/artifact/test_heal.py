"""
@file_name: test_heal.py
@author: Bin Liang
@date: 2026-07-21
@description: Tests for the broken-pointer recovery strategy (ArtifactService.heal).

Covers every branch of the recovery sequence:
- artifact missing / owned by another agent → ArtifactNotFound
- pointer already valid → recovered, no re-registration
- caller-picked entry_path → re-register onto the same artifact_id
- caller-picked entry_path rejected → ArtifactError propagates
- workspace scan: unique match auto-recovers; zero and multiple matches
  return candidates without recovering
"""
from __future__ import annotations

import os

import pytest

from narranexus.platform.artifact import (
    ArtifactNotFound,
    ArtifactPathEscape,
    ArtifactService,
)
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

    service = ArtifactService(db_client)
    repo = ArtifactRepository(db_client)
    registered = await service.register(
        agent_id="agent_x", user_id="user_y", session_id=None,
        kind="text/html", entry_path=str(entry),
        title="report", description=None, target_artifact_id=None,
    )
    yield {
        "db": db_client,
        "service": service,
        "repo": repo,
        "workspace": workspace,
        "entry": entry,
        "artifact_id": registered.artifact_id,
    }


@pytest.mark.asyncio
async def test_heal_unknown_artifact_raises_not_found(env):
    with pytest.raises(ArtifactNotFound):
        await env["service"].heal(
            agent_id="agent_x", user_id="user_y", artifact_id="art_missing",
        )


@pytest.mark.asyncio
async def test_heal_artifact_of_other_agent_raises_not_found(env):
    """Ownership mismatch is indistinguishable from absence (no existence leak)."""
    with pytest.raises(ArtifactNotFound):
        await env["service"].heal(
            agent_id="agent_other", user_id="user_y", artifact_id=env["artifact_id"],
        )


@pytest.mark.asyncio
async def test_heal_valid_pointer_short_circuits(env):
    """Entry still on disk → recovered immediately, pointer untouched."""
    before = await env["repo"].get_by_id(env["artifact_id"])
    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
    )
    assert result.recovered is True
    assert result.artifact is not None
    assert result.artifact.file_path == before.file_path
    assert "already valid" in result.message


@pytest.mark.asyncio
async def test_heal_with_picked_entry_path_reregisters(env):
    """User picked a candidate → pointer moves onto the picked path, same id."""
    env["entry"].unlink()  # break the pointer
    (env["workspace"] / "fresh").mkdir()
    fresh = env["workspace"] / "fresh" / "new.html"
    fresh.write_text("<p>new</p>", encoding="utf-8")

    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
        entry_path="fresh/new.html",
    )
    assert result.recovered is True
    row = await env["repo"].get_by_id(env["artifact_id"])
    assert row.file_path == f"{WS_REL}/fresh/new.html"


@pytest.mark.asyncio
async def test_heal_with_bad_entry_path_propagates_error(env):
    """A rejected pick (outside workspace) surfaces the structured error."""
    env["entry"].unlink()
    with pytest.raises(ArtifactPathEscape):
        await env["service"].heal(
            agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
            entry_path="/etc/passwd",
        )


@pytest.mark.asyncio
async def test_heal_scan_unique_match_auto_recovers(env):
    """Broken pointer + exactly one kind-matching file → auto re-register."""
    env["entry"].unlink()
    (env["workspace"] / "rebuilt").mkdir()
    rebuilt = env["workspace"] / "rebuilt" / "index.html"
    rebuilt.write_text("<p>rebuilt</p>", encoding="utf-8")

    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
    )
    assert result.recovered is True
    assert "auto-recovered" in result.message
    row = await env["repo"].get_by_id(env["artifact_id"])
    assert row.file_path == f"{WS_REL}/rebuilt/index.html"


@pytest.mark.asyncio
async def test_heal_scan_zero_matches_returns_empty_candidates(env):
    env["entry"].unlink()
    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
    )
    assert result.recovered is False
    assert result.candidates == []
    assert "no matching file" in result.message


@pytest.mark.asyncio
async def test_heal_scan_multiple_matches_returns_candidates_newest_first(env):
    env["entry"].unlink()
    (env["workspace"] / "a").mkdir()
    older = env["workspace"] / "a" / "older.html"
    older.write_text("<p>a</p>", encoding="utf-8")
    (env["workspace"] / "b").mkdir()
    newer = env["workspace"] / "b" / "newer.html"
    newer.write_text("<p>b</p>", encoding="utf-8")
    # Force a deterministic mtime ordering regardless of filesystem timing.
    os.utime(older, (1000, 1000))
    os.utime(newer, (2000, 2000))

    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
    )
    assert result.recovered is False
    paths = [c.workspace_path for c in result.candidates]
    assert paths == ["b/newer.html", "a/older.html"]
    # Not registered onto anything — pointer stays broken until the user picks.
    row = await env["repo"].get_by_id(env["artifact_id"])
    assert row.file_path == f"{WS_REL}/report/index.html"


# ---------------------------------------------------------------------------
# Hash tier + guardrails (2026-08-18, spec artifact-events §5): heal verifies
# candidates against the stored content_hash before guessing by extension,
# never offers another live artifact's file, and every repoint is attributed
# (history action="healed") and broadcast (outbox action="repointed").
# ---------------------------------------------------------------------------

async def _history_actions(db, artifact_id):
    rows = await db.execute(
        "SELECT action FROM instance_artifact_history WHERE artifact_id = %s ORDER BY id",
        params=(artifact_id,), fetch=True)
    return [r["action"] for r in rows]


async def _outbox_events(db, agent_id="agent_x"):
    import json
    rows = await db.execute(
        "SELECT payload_json FROM instance_artifact_events WHERE agent_id = %s ORDER BY id",
        params=(agent_id,), fetch=True)
    return [json.loads(r["payload_json"]) for r in rows]


@pytest.mark.asyncio
async def test_heal_hash_match_beats_extension_ambiguity(env):
    """Two .html candidates, one with identical bytes → hash picks it
    deterministically where the extension tier would have gone to a modal."""
    original_bytes = env["entry"].read_bytes()
    renamed = env["workspace"] / "report" / "draft.html"
    renamed.write_bytes(original_bytes)
    decoy = env["workspace"] / "report" / "unrelated.html"
    decoy.write_text("<p>something else</p>", encoding="utf-8")
    os.remove(env["entry"])

    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
    )

    assert result.recovered is True
    assert result.artifact.file_path.endswith("report/draft.html")
    assert (await _history_actions(env["db"], env["artifact_id"]))[-1] == "healed"
    last = (await _outbox_events(env["db"]))[-1]
    assert last["action"] == "repointed"
    assert last["extra"]["hash_matched"] is True
    assert last["extra"]["new"].endswith("draft.html")


@pytest.mark.asyncio
async def test_heal_multiple_hash_matches_go_to_modal(env):
    """Copies (same bytes) are ambiguous intent — never auto-pick one."""
    original_bytes = env["entry"].read_bytes()
    copy_a = env["workspace"] / "report" / "copy_a.html"
    copy_a.write_bytes(original_bytes)
    copy_b = env["workspace"] / "report" / "copy_b.html"
    copy_b.write_bytes(original_bytes)
    os.remove(env["entry"])

    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
    )

    assert result.recovered is False
    names = {c.workspace_path for c in result.candidates}
    assert names == {"report/copy_a.html", "report/copy_b.html"}


@pytest.mark.asyncio
async def test_heal_falls_back_to_extension_tier_when_hash_misses(env):
    """Renamed AND edited → hash can't claim it; the single-extension-match
    auto-recover stays (declared boundary), flagged as unverified."""
    changed = env["workspace"] / "report" / "rewritten.html"
    changed.write_text("<p>edited after rename</p>", encoding="utf-8")
    os.remove(env["entry"])

    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
    )

    assert result.recovered is True
    assert result.artifact.file_path.endswith("report/rewritten.html")
    last = (await _outbox_events(env["db"]))[-1]
    assert last["action"] == "repointed"
    assert last["extra"]["hash_matched"] is False


@pytest.mark.asyncio
async def test_heal_never_offers_another_live_artifacts_file(env):
    """Guardrail: a candidate that is some OTHER artifact's current pointer
    target must be excluded — repointing there would collapse two artifacts
    onto one file."""
    other_entry = env["workspace"] / "report" / "other.html"
    other_entry.write_bytes(env["entry"].read_bytes())  # same bytes: worst case
    await env["service"].register(
        agent_id="agent_x", user_id="user_y", session_id=None,
        kind="text/html", entry_path=str(other_entry),
        title="other", description=None, target_artifact_id=None,
    )
    os.remove(env["entry"])

    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
    )

    assert result.recovered is False
    assert result.candidates == []


@pytest.mark.asyncio
async def test_heal_user_pick_is_attributed_and_broadcast(env):
    """The modal path (explicit entry_path) gets the same honesty treatment
    as auto-repoints: history says healed, the outbox says repointed."""
    picked = env["workspace"] / "report" / "picked.html"
    picked.write_text("<p>picked</p>", encoding="utf-8")
    os.remove(env["entry"])

    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
        entry_path="report/picked.html",
    )

    assert result.recovered is True
    assert (await _history_actions(env["db"], env["artifact_id"]))[-1] == "healed"
    last = (await _outbox_events(env["db"]))[-1]
    assert last["action"] == "repointed"
    assert last["extra"]["hash_matched"] is False


# --- heal never scans or hashes another tenant's tree --------------------------

def _victim_workspace(env):
    """A different tenant's workspace next to ours, under the same base."""
    victim = env["workspace"].parent.parent / agent_workspace_relpath("agent_v", "user_v")
    (victim / "report").mkdir(parents=True)
    (victim / "report" / "index.html").write_text("<p>victim</p>", encoding="utf-8")
    (victim / "notes.html").write_text("<p>victim notes</p>", encoding="utf-8")
    return victim


@pytest.mark.asyncio
async def test_heal_refuses_a_workspace_swapped_for_a_link(env):
    """The tenant replaces its own workspace dir with a link to the victim's.
    Heal must neither call the pointer "already valid" (the victim happens to
    hold report/index.html), nor list or hash the victim's files, nor repoint."""
    import shutil

    victim = _victim_workspace(env)
    before = await env["repo"].get_by_id(env["artifact_id"])
    shutil.rmtree(env["workspace"])
    os.symlink(victim, env["workspace"])

    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
    )
    assert result.recovered is False
    assert result.candidates == []
    assert "victim" not in result.message
    row = await env["repo"].get_by_id(env["artifact_id"])
    assert row.file_path == before.file_path
    assert row.content_hash == before.content_hash


@pytest.mark.asyncio
async def test_heal_skips_a_file_link_that_points_outside_the_workspace(env):
    """A link inside the workspace whose target is the victim's file (same bytes
    as our lost entry, so the hash tier would match) is not a candidate: it is
    neither offered, nor stat-ed for size/mtime, nor hashed."""
    victim = _victim_workspace(env)
    (victim / "report" / "index.html").write_bytes(env["entry"].read_bytes())
    env["entry"].unlink()
    os.symlink(victim / "report" / "index.html", env["workspace"] / "report" / "leak.html")

    result = await env["service"].heal(
        agent_id="agent_x", user_id="user_y", artifact_id=env["artifact_id"],
    )
    assert result.recovered is False
    assert result.candidates == []
