"""
@file_name: test_install_skill_errors.py
@author: Bin Liang
@date: 2026-05-27
@description: install_skill must reject bad zip packages with messages that
tell the user *what* is wrong AND *what to do*. Generic strings like
"too many files" force the user to open Network DevTools, find the request,
and guess — exactly the support burden this commit set out to fix.

Each test pins one rejection path with a specific zip payload and asserts
the message carries the actionable detail (file count, MB count, offending
path, expected SKILL.md location).
"""
from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from xyz_agent_context.module.skill_module.skill_module import SkillModule


@pytest.fixture
def skill_module(tmp_path):
    """SkillModule whose skills_dir is a fresh tmp directory."""
    module = SkillModule(agent_id="agent_test", user_id="test_user")
    module.skills_dir = tmp_path / "skills"
    module.skills_dir.mkdir(parents=True, exist_ok=True)
    return module


def _write_zip(target: Path, entries: dict[str, bytes | str]) -> Path:
    """Build a zip at ``target`` with the given ``{name: content}`` mapping."""
    with zipfile.ZipFile(target, "w") as zf:
        for name, content in entries.items():
            if isinstance(content, str):
                content = content.encode("utf-8")
            zf.writestr(name, content)
    return target


# -------- SKILL.md missing: message must point user at the fix ---------


def test_missing_skill_md_message_explains_expected_location(skill_module, tmp_path):
    zip_path = _write_zip(tmp_path / "no-skill-md.zip", {"some-file.txt": "hello"})
    with pytest.raises(ValueError) as exc_info:
        skill_module.install_skill(zip_path)
    msg = str(exc_info.value)
    assert "SKILL.md" in msg
    # Tell the user where SKILL.md is expected: root or single top-level dir
    assert "root" in msg.lower() or "top-level" in msg.lower() or "subfolder" in msg.lower()


# -------- too many files: message must carry actual count + limit ------


def test_too_many_files_message_includes_count_and_limit(skill_module, tmp_path):
    # Build a zip with > 500 entries (501 dummy files)
    entries = {f"file_{i}.txt": f"content {i}" for i in range(501)}
    zip_path = _write_zip(tmp_path / "huge-count.zip", entries)
    with pytest.raises(ValueError) as exc_info:
        skill_module.install_skill(zip_path)
    msg = str(exc_info.value)
    assert "501" in msg
    assert "500" in msg


# -------- uncompressed size too large: message must carry size + limit -


def test_uncompressed_size_too_large_message_includes_mb(skill_module, tmp_path):
    # One ~120MB file (above the 100MB limit)
    big_content = b"A" * (120 * 1024 * 1024)
    zip_path = _write_zip(tmp_path / "huge-size.zip", {"big.bin": big_content})
    with pytest.raises(ValueError) as exc_info:
        skill_module.install_skill(zip_path)
    msg = str(exc_info.value)
    assert "MB" in msg
    assert "100" in msg  # the limit


# -------- path traversal: message must include offending path ----------


def test_path_traversal_message_includes_offending_path(skill_module, tmp_path):
    # Manually write a zip with a traversal entry. zipfile.writestr accepts
    # any string as the entry name, including paths it would normally reject.
    zip_path = tmp_path / "evil.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        zf.writestr("../../etc/evil.txt", "pwn")
    with pytest.raises(ValueError) as exc_info:
        skill_module.install_skill(zip_path)
    msg = str(exc_info.value)
    assert "traversal" in msg.lower()
    assert "../../etc/evil.txt" in msg or "etc/evil.txt" in msg


# -------- happy path: zip with SKILL.md installs cleanly ---------------


def test_happy_path_installs_skill_with_skill_md_at_root(skill_module, tmp_path):
    skill_md = (
        "---\n"
        "name: hello-world\n"
        "description: A simple hello world skill\n"
        "---\n\n# Hello\n"
    )
    zip_path = _write_zip(tmp_path / "hello.zip", {"SKILL.md": skill_md})
    info = skill_module.install_skill(zip_path)
    assert info.name == "hello-world"


def test_happy_path_installs_skill_with_skill_md_in_subfolder(skill_module, tmp_path):
    skill_md = (
        "---\n"
        "name: hello-world\n"
        "description: hi\n"
        "---\n\n# Hello\n"
    )
    zip_path = _write_zip(
        tmp_path / "hello.zip",
        {"hello-world/SKILL.md": skill_md},
    )
    info = skill_module.install_skill(zip_path)
    assert info.name == "hello-world"


# -------- Windows-zipped skill (backslashes) installs cleanly ---------


def test_windows_zipped_skill_installs_after_backslash_normalization(skill_module, tmp_path):
    """A zip created by Windows Explorer uses `\\` separators in entry names.

    This test bakes a zip where the entry name literally contains a backslash
    and asserts the install succeeds — pinning the fix in
    `validate_zip_member_path` end-to-end."""
    skill_md = (
        "---\n"
        "name: win-skill\n"
        "description: from Windows Explorer\n"
        "---\n\n# Win\n"
    )
    zip_path = tmp_path / "winzip.zip"
    with zipfile.ZipFile(zip_path, "w") as zf:
        # NOTE: forcing an entry with a backslash separator (mimicking
        # Windows Explorer "Send to > Compressed folder").
        zf.writestr("win-skill\\SKILL.md", skill_md)
    info = skill_module.install_skill(zip_path)
    assert info.name == "win-skill"
