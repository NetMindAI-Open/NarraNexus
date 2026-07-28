"""
@file_name: test_attachment_staging.py
@date: 2026-07-20
@description: Bus attachment staging — resolve send-time handles (att_ file_id
and workspace-relative paths), hard-link into the per-user shared area, reject
workspace escapes, and render Read-tool markers.

"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from xyz_agent_context.message_bus._bus_attachment_impl import (
    build_bus_markers,
    load_bus_attachment_meta,
    resolve_and_stage_refs,
    resolve_shared_file_by_id,
    resolve_shared_file_for_user,
    stage_path_into_team,
    store_bus_attachment_meta,
    store_bytes_into_bus,
)
from xyz_agent_context.utils.workspace_paths import (
    agent_workspace_path,
    bus_files_dir,
    team_shared_dir,
)

AGENT = "agent_sender01"
OWNER = "user_owner01"


def _write_workspace_file(base: Path, rel: str, data: bytes) -> None:
    p = agent_workspace_path(AGENT, OWNER, base=str(base)) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(data)


@pytest.mark.asyncio
async def test_stage_workspace_path_hardlinks_into_shared(tmp_path):
    _write_workspace_file(tmp_path, "work/report.pdf", b"%PDF-1.4 hello")

    staged = await resolve_and_stage_refs(
        sender_agent_id=AGENT,
        owner_user_id=OWNER,
        refs=["work/report.pdf"],
        base=str(tmp_path),
    )

    assert len(staged) == 1
    att = staged[0]
    assert att["file_id"].startswith("att_")
    assert att["original_name"] == "report.pdf"
    assert att["mime_type"] == "application/pdf"
    assert att["category"] == "document"
    # rel_path lands under the per-user shared bus_files area
    assert att["rel_path"].startswith(f"{OWNER}/_shared/bus_files/")

    abs_staged = tmp_path / att["rel_path"]
    assert abs_staged.is_file()
    assert abs_staged.read_bytes() == b"%PDF-1.4 hello"
    # hard-link: same inode as the source (zero-copy on one filesystem)
    src = agent_workspace_path(AGENT, OWNER, base=str(tmp_path)) / "work/report.pdf"
    assert abs_staged.stat().st_ino == src.stat().st_ino


@pytest.mark.asyncio
async def test_stage_by_attachment_file_id(tmp_path, monkeypatch):
    # att_ handles resolve through the sender's user_upload_files store, which
    # keys off settings.base_working_path — point it at tmp_path.
    from xyz_agent_context import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "base_working_path", str(tmp_path))
    from xyz_agent_context.utils.attachment_storage import store_uploaded_attachment

    file_id, _ = store_uploaded_attachment(
        AGENT,
        OWNER,
        raw_bytes=b"\x89PNG data",
        original_name="chart.png",
        mime_type="image/png",
    )

    staged = await resolve_and_stage_refs(
        sender_agent_id=AGENT,
        owner_user_id=OWNER,
        refs=[file_id],
    )

    assert len(staged) == 1
    assert staged[0]["category"] == "image"
    assert (tmp_path / staged[0]["rel_path"]).read_bytes() == b"\x89PNG data"


@pytest.mark.asyncio
async def test_workspace_escape_ref_is_rejected(tmp_path):
    # A secret outside the sender workspace must never be stageable.
    (tmp_path / "secret.txt").write_bytes(b"top secret")
    staged = await resolve_and_stage_refs(
        sender_agent_id=AGENT,
        owner_user_id=OWNER,
        refs=["../../secret.txt"],
        base=str(tmp_path),
    )
    assert staged == []


@pytest.mark.asyncio
async def test_missing_ref_is_skipped_not_raised(tmp_path):
    staged = await resolve_and_stage_refs(
        sender_agent_id=AGENT,
        owner_user_id=OWNER,
        refs=["work/does_not_exist.txt"],
        base=str(tmp_path),
    )
    assert staged == []


@pytest.mark.asyncio
async def test_copy_fallback_when_hardlink_fails(tmp_path, monkeypatch):
    _write_workspace_file(tmp_path, "a.txt", b"payload")

    def _boom(src, dst):
        raise OSError("EXDEV: cross-device link")

    monkeypatch.setattr(os, "link", _boom)
    staged = await resolve_and_stage_refs(
        sender_agent_id=AGENT,
        owner_user_id=OWNER,
        refs=["a.txt"],
        base=str(tmp_path),
    )
    assert len(staged) == 1
    abs_staged = tmp_path / staged[0]["rel_path"]
    assert abs_staged.read_bytes() == b"payload"
    # copy (not link): distinct inode
    src = agent_workspace_path(AGENT, OWNER, base=str(tmp_path)) / "a.txt"
    assert abs_staged.stat().st_ino != src.stat().st_ino


def test_build_bus_markers_shape(tmp_path):
    atts = [
        {
            "file_id": "att_1234abcd",
            "original_name": "report.pdf",
            "mime_type": "application/pdf",
            "size_bytes": 10,
            "category": "document",
            "rel_path": f"{OWNER}/_shared/bus_files/2026-07-20/att_1234abcd.pdf",
        }
    ]
    marker = build_bus_markers(atts, from_agent="agent_x", base=str(tmp_path))
    assert "use Read tool" in marker
    assert "name=report.pdf" in marker
    assert "from agent agent_x" in marker
    assert str(tmp_path) in marker  # absolute path rebuilt from base


def test_build_bus_markers_empty():
    assert build_bus_markers(None) == ""
    assert build_bus_markers([]) == ""


@pytest.mark.asyncio
async def test_rel_path_clean_under_symlinked_base(tmp_path):
    # A symlinked base (e.g. macOS /var → /private/var) must not produce a
    # rel_path with ".." — that escaped path breaks the marker + serving.
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)

    p = agent_workspace_path(AGENT, OWNER, base=str(link)) / "a.txt"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"hi")

    staged = await resolve_and_stage_refs(
        sender_agent_id=AGENT, owner_user_id=OWNER, refs=["a.txt"], base=str(link)
    )
    rel = staged[0]["rel_path"]
    assert ".." not in rel.split("/")
    assert rel.startswith(f"{OWNER}/_shared/bus_files/")
    # Resolves + serves through the symlinked base too.
    assert resolve_shared_file_for_user(OWNER, rel, base=str(link)) is not None


@pytest.mark.asyncio
async def test_stage_into_team_dir(tmp_path):
    _write_workspace_file(tmp_path, "plan.md", b"# plan")
    staged = await stage_path_into_team(
        sender_agent_id=AGENT,
        owner_user_id=OWNER,
        team_id="team_abc",
        ref="plan.md",
        base=str(tmp_path),
    )
    assert staged is not None
    dest = team_shared_dir(OWNER, "team_abc", base=str(tmp_path))
    assert Path(staged["path"]).parent == dest.resolve()
    assert Path(staged["path"]).read_bytes() == b"# plan"


@pytest.mark.asyncio
async def test_resolve_shared_file_for_user_serves_and_gates(tmp_path):
    _write_workspace_file(tmp_path, "doc.txt", b"hello")
    staged = await resolve_and_stage_refs(
        sender_agent_id=AGENT, owner_user_id=OWNER,
        refs=["doc.txt"], base=str(tmp_path),
    )
    rel = staged[0]["rel_path"]

    # Owner resolves their own file.
    got = resolve_shared_file_for_user(OWNER, rel, base=str(tmp_path))
    assert got is not None and got.read_bytes() == b"hello"

    # Another user cannot fetch it (path's first segment != user_id).
    assert resolve_shared_file_for_user("user_intruder", rel, base=str(tmp_path)) is None
    # Traversal outside the user root is rejected.
    assert resolve_shared_file_for_user(OWNER, f"{OWNER}/../secret", base=str(tmp_path)) is None
    # Missing file → None.
    assert resolve_shared_file_for_user(OWNER, f"{OWNER}/_shared/bus_files/x/none.txt", base=str(tmp_path)) is None


@pytest.mark.asyncio
async def test_store_bytes_into_bus_roundtrip(tmp_path):
    # A user upload has no source workspace file — bytes are written directly.
    att = await store_bytes_into_bus(
        user_id=OWNER,
        raw_bytes=b"hello world",
        original_name="notes.txt",
        mime_type="text/plain",
        base=str(tmp_path),
    )
    assert att["file_id"].startswith("att_")
    assert att["original_name"] == "notes.txt"
    assert att["mime_type"] == "text/plain"
    assert att["category"] == "code"  # text/plain → CODE category
    assert att["size_bytes"] == 11
    assert att["rel_path"].startswith(f"{OWNER}/_shared/bus_files/")
    # Resolvable + readable by the same user (the download path).
    got = resolve_shared_file_for_user(OWNER, att["rel_path"], base=str(tmp_path))
    assert got is not None and got.read_bytes() == b"hello world"


@pytest.mark.asyncio
async def test_resolve_shared_file_by_id_prefers_original_over_mp3(tmp_path):
    # A voice memo (webm) with a later .mp3 transcode sibling: by-id resolution
    # (used by the public transcription endpoint) must return the original.
    att = await store_bytes_into_bus(
        user_id=OWNER, raw_bytes=b"webmdata", original_name="memo.webm",
        mime_type="audio/webm", base=str(tmp_path),
    )
    fid = att["file_id"]
    original = tmp_path / att["rel_path"]
    (original.with_suffix(".mp3")).write_bytes(b"mp3cache")  # transcode sibling

    got = resolve_shared_file_by_id(OWNER, fid, base=str(tmp_path))
    assert got is not None and got.suffix == ".webm"
    # Invalid id / unknown user → None.
    assert resolve_shared_file_by_id(OWNER, "not_an_id", base=str(tmp_path)) is None
    assert resolve_shared_file_by_id("someone_else", fid, base=str(tmp_path)) is None


def test_build_bus_markers_includes_transcript(tmp_path):
    atts = [{
        "file_id": "att_1234abcd", "original_name": "memo.webm",
        "mime_type": "audio/webm", "size_bytes": 8, "category": "media",
        "rel_path": f"{OWNER}/_shared/bus_files/2026-07-21/att_1234abcd.webm",
        "transcript": "hello team", "source": "recording",
    }]
    marker = build_bus_markers(atts, base=str(tmp_path))
    assert "transcript=hello team" in marker
    assert "use Read tool" in marker


@pytest.mark.asyncio
async def test_attachment_meta_sidecar_roundtrip(tmp_path):
    # The upload endpoint persists the finished dict server-side; the send
    # endpoint reloads it instead of trusting the client's echoed copy.
    att = await store_bytes_into_bus(
        user_id=OWNER, raw_bytes=b"webmdata", original_name="memo.webm",
        mime_type="audio/webm", base=str(tmp_path),
    )
    att["source"] = "recording"
    att["transcript"] = "hello team"
    store_bus_attachment_meta(OWNER, att, base=str(tmp_path))

    loaded = load_bus_attachment_meta(OWNER, att["rel_path"], base=str(tmp_path))
    assert loaded == att
    # The `_meta.json` sidecar must never shadow the original in by-id
    # resolution (its name deliberately misses the `{file_id}.*` glob).
    got = resolve_shared_file_by_id(OWNER, att["file_id"], base=str(tmp_path))
    assert got is not None and got.suffix == ".webm"
    # Same user-scoping gate as the file itself; missing sidecar → None.
    assert load_bus_attachment_meta("user_intruder", att["rel_path"], base=str(tmp_path)) is None
    assert load_bus_attachment_meta(OWNER, f"{OWNER}/_shared/bus_files/x/none.txt", base=str(tmp_path)) is None
