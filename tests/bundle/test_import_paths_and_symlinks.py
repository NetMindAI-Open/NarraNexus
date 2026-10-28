"""
@file_name: test_import_paths_and_symlinks.py
@author: Bin Liang
@date: 2026-10-28
@description: Bundle contents are attacker-controlled. Pins:
- ``artifacts.json`` ``file_path`` values cannot climb out of the new agent's
  workspace (``..``, absolute, backslash) — both the validator and the row
  rewrite the import loop calls (``rewrite_artifact_row``);
- copying a skill dir into an agent never DEREFERENCES symlinks (a link to
  /etc/passwd stays a link, its bytes are not materialised in the workspace);
- the full-copy exporter never follows symlinks into the archive;
- the tar extractor uses the stdlib ``data`` filter.
"""
from __future__ import annotations

import os
import zipfile

import pytest

from narranexus.platform.bundle import builder, importer


@pytest.mark.parametrize("bad", ["../x.html", "a/../../x.html", "..\\x.html", "a/..", "", "x\x00y", "//etc/passwd/.."])
def test_artifact_relpath_rejects_escapes(bad):
    with pytest.raises(ValueError):
        importer.artifact_relpath_or_raise(bad)


def test_rewrite_artifact_row_rejects_a_climbing_pointer():
    with pytest.raises(ValueError):
        importer.rewrite_artifact_row(
            {"artifact_id": "art_1", "file_path": "../../user_victim/agent_v/x.html"},
            "agent_0123456789ab", "user_x",
        )


def test_rewrite_artifact_row_prefixes_the_new_workspace_and_resets_session_state():
    row = importer.rewrite_artifact_row(
        {
            "artifact_id": "art_1", "file_path": "/report/index.html", "user_id": "user_old",
            "session_id": "s", "original_session_id": "s0", "pinned": 0,
            "created_at": "t", "updated_at": "t",
        },
        "agent_0123456789ab", "user_x",
    )
    assert row["file_path"] == "user_x/agent_0123456789ab/report/index.html"
    assert row["user_id"] == "user_x"
    assert row["session_id"] is None and row["original_session_id"] is None
    assert row["pinned"] == 1
    assert "created_at" not in row and "updated_at" not in row


def test_rewrite_artifact_row_keeps_an_empty_pointer_empty():
    row = importer.rewrite_artifact_row({"artifact_id": "art_1", "file_path": ""}, "agent_0123456789ab", "user_x")
    assert row["file_path"] == ""


@pytest.mark.parametrize("ok", ["report/index.html", "index.html", "a/b/c.md", "/leading/slash.html"])
def test_artifact_relpath_accepts_workspace_relative(ok):
    assert importer.artifact_relpath_or_raise(ok) == ok.lstrip("/")


def test_copy_skill_dir_keeps_symlinks_as_links(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("host-secret", encoding="utf-8")
    src = tmp_path / "src_skill"
    src.mkdir()
    (src / "SKILL.md").write_text("# s", encoding="utf-8")
    os.symlink(outside, src / "leak.txt")
    dst = tmp_path / "dst_skill"
    importer.copy_skill_dir(src, dst)
    assert (dst / "SKILL.md").read_text(encoding="utf-8") == "# s"
    assert (dst / "leak.txt").is_symlink()
    assert os.readlink(dst / "leak.txt") == str(outside)
    assert (dst / "leak.txt").lstat().st_size != len("host-secret")  # link, not a copy


def test_copy_skill_into_agent_keeps_symlinks_as_links(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("host-secret", encoding="utf-8")
    src = tmp_path / "src_skill"
    src.mkdir()
    (src / "SKILL.md").write_text("# s", encoding="utf-8")
    os.symlink(outside, src / "leak.txt")
    base = tmp_path / "ws"
    target = importer.copy_skill_into_agent(src, "agent_0123456789ab", "user_x", "my-skill", base=str(base))
    assert target == base / "user_x" / "agent_0123456789ab" / "skills" / "my-skill"
    assert (target / "leak.txt").is_symlink()
    assert os.readlink(target / "leak.txt") == str(outside)


def test_tar_extractor_drops_fifo_and_link_members(tmp_path):
    import io
    import tarfile
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        data = b"hello"
        ti = tarfile.TarInfo("ok.txt")
        ti.size = len(data)
        tar.addfile(ti, io.BytesIO(data))
        fifo = tarfile.TarInfo("pipe")
        fifo.type = tarfile.FIFOTYPE
        tar.addfile(fifo)
        link = tarfile.TarInfo("leak")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
        suid = tarfile.TarInfo("suid.sh")
        suid.size = 1
        suid.mode = 0o4755
        tar.addfile(suid, io.BytesIO(b"x"))
    tar_path = tmp_path / "ws.tar.gz"
    tar_path.write_bytes(buf.getvalue())
    out = tmp_path / "out"
    out.mkdir()
    importer._extract_tar_safely(tar_path, out)
    assert (out / "ok.txt").read_bytes() == b"hello"
    assert not (out / "pipe").exists() and not (out / "leak").exists() and not (out / "leak").is_symlink()
    assert (out / "suid.sh").stat().st_mode & 0o4000 == 0


def test_zip_dir_skips_symlinks(tmp_path):
    outside = tmp_path / "outside.txt"
    outside.write_text("host-secret", encoding="utf-8")
    src = tmp_path / "skill"
    src.mkdir()
    (src / "SKILL.md").write_text("# s", encoding="utf-8")
    os.symlink(outside, src / "leak.txt")
    dst = tmp_path / "out.zip"
    builder._zip_dir(src, dst, scrub_secrets=False)
    with zipfile.ZipFile(dst) as z:
        names = z.namelist()
    assert "SKILL.md" in names
    assert "leak.txt" not in names


def test_exporters_refuse_a_symlinked_source_dir(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (real / "a.txt").write_text("a", encoding="utf-8")
    link = tmp_path / "link"
    os.symlink(real, link)
    with pytest.raises(ValueError):
        builder._zip_dir(link, tmp_path / "o.zip")
    with pytest.raises(ValueError):
        builder._pack_workspace_sync(link, tmp_path / "o.tar.gz", set())
