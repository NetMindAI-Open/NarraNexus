"""
@file_name: test_file_safety.py
@author: Bin Liang
@date: 2026-05-27
@description: validate_zip_member_path must accept Windows-style backslashes
by normalizing them to forward slashes BEFORE running traversal/absolute-path
checks. Windows Explorer's built-in "Send to > Compressed folder" writes
backslash separators into ZIP entries, and that should not block users from
installing a perfectly safe skill package.

The original implementation blanket-rejected any backslash with the opaque
message "Invalid archive entry: unsafe path", which both failed legitimate
Windows-zipped packages and gave the user no actionable hint.

These tests pin:
1. Backslashes are normalized to forward slashes (legitimate Windows zips work).
2. Traversal/absolute-path checks still fire on normalized paths.
3. Null bytes remain rejected with a clear message.
"""
from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from xyz_agent_context.utils.file_safety import validate_zip_member_path


# -------- normalization: Windows-zipped packages must work --------------


def test_normalizes_single_backslash_to_slash():
    assert validate_zip_member_path("dir\\file.txt") == PurePosixPath("dir/file.txt")


def test_normalizes_multiple_backslashes():
    assert validate_zip_member_path("a\\b\\c.py") == PurePosixPath("a/b/c.py")


def test_top_level_skill_md_with_backslash_prefix():
    # `test-skill\SKILL.md` from a Windows Explorer zip is still safe
    assert validate_zip_member_path("test-skill\\SKILL.md") == PurePosixPath("test-skill/SKILL.md")


def test_forward_slashes_unchanged():
    assert validate_zip_member_path("dir/file.txt") == PurePosixPath("dir/file.txt")


# -------- traversal still rejected after normalization ------------------


def test_rejects_traversal_with_backslashes():
    with pytest.raises(ValueError, match="traversal"):
        validate_zip_member_path("..\\evil")


def test_rejects_embedded_traversal_with_backslashes():
    with pytest.raises(ValueError, match="traversal"):
        validate_zip_member_path("foo\\..\\bar")


def test_rejects_forward_slash_traversal():
    with pytest.raises(ValueError, match="traversal"):
        validate_zip_member_path("../escape")


# -------- absolute paths still rejected ---------------------------------


def test_rejects_posix_absolute_path():
    with pytest.raises(ValueError, match="absolute"):
        validate_zip_member_path("/etc/passwd")


def test_rejects_backslash_unc_style_absolute_path():
    # `\\server\share` normalizes to `//server/share` → still absolute
    with pytest.raises(ValueError, match="absolute"):
        validate_zip_member_path("\\\\server\\share")


# -------- null bytes still rejected with clear message ------------------


def test_rejects_null_byte_with_clear_message():
    with pytest.raises(ValueError, match="null"):
        validate_zip_member_path("foo\x00bar")


# -------- empty / missing path still rejected ---------------------------


def test_rejects_empty_path():
    with pytest.raises(ValueError, match="empty"):
        validate_zip_member_path("")
