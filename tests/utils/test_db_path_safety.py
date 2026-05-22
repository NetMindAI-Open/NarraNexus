"""
@file_name: test_db_path_safety.py
@author: Bin Liang
@date: 2026-05-22
@description: SQLite path + parent-dir safety.

Two fixes for the fresh-Mac "Connection failed" chain:
1. parse_sqlite_url collapses the malformed leading `//` (from `sqlite:///` +
   an already-absolute path) so the DB path is `/Users/...` not `//Users/...`.
2. SQLiteBackend.initialize raises a CLEAR, actionable error (not the cryptic
   `unable to open database file`) when ~/.narranexus is unwritable — after
   trying to repair perms on a dir we own.
"""
import os

import pytest

from xyz_agent_context.utils.db_factory import parse_sqlite_url
from xyz_agent_context.utils.db_backend_sqlite import SQLiteBackend


def test_parse_collapses_double_slash():
    assert parse_sqlite_url("sqlite:////Users/x/.narranexus/nexus.db") == "/Users/x/.narranexus/nexus.db"


def test_parse_keeps_single_slash_absolute():
    assert parse_sqlite_url("sqlite:///Users/x/db.sqlite") == "/Users/x/db.sqlite"


def test_parse_keeps_relative():
    assert parse_sqlite_url("sqlite:///data/db.sqlite") == "/data/db.sqlite"


@pytest.mark.asyncio
async def test_init_ok_when_parent_writable(tmp_path):
    be = SQLiteBackend(str(tmp_path / "sub" / "nexus.db"))
    await be.initialize()
    try:
        assert (tmp_path / "sub" / "nexus.db").exists()
    finally:
        await be.close()


@pytest.mark.skipif(not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="needs POSIX ownership + non-root")
@pytest.mark.asyncio
async def test_init_raises_clear_error_when_parent_unwritable(tmp_path):
    ro = tmp_path / "ro"
    ro.mkdir()
    os.chmod(ro, 0o500)  # can't create children; outside $HOME so no self-repair
    be = SQLiteBackend(str(ro / "sub" / "nexus.db"))
    try:
        with pytest.raises(RuntimeError, match="not writable"):
            await be.initialize()
    finally:
        os.chmod(ro, 0o700)


@pytest.mark.asyncio
async def test_memory_db_skips_dir_guard():
    be = SQLiteBackend(":memory:")
    await be.initialize()  # must not raise / not touch the filesystem
    await be.close()
