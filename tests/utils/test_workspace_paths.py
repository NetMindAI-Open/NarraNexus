"""
@file_name: test_workspace_paths.py
@date: 2026-06-17
@description: Central workspace-layout helper. Locks the current (flat)
behaviour so the 3a conversion is provably identical, and locks the
nested-layout shape that 3b will switch on.
"""
from __future__ import annotations

from pathlib import Path

import xyz_agent_context.utils.workspace_paths as wp


def test_flat_layout_matches_legacy(monkeypatch):
    monkeypatch.setattr(wp, "_LAYOUT", "flat")
    assert wp.agent_workspace_relpath("agent_x", "user_y") == "agent_x_user_y"
    assert wp.agent_workspace_path("agent_x", "user_y", base="/ws") == Path("/ws/agent_x_user_y")


def test_nested_layout_is_user_then_agent(monkeypatch):
    monkeypatch.setattr(wp, "_LAYOUT", "nested")
    assert wp.agent_workspace_relpath("agent_x", "user_y") == "user_y/agent_x"
    assert wp.agent_workspace_path("agent_x", "user_y", base="/ws") == Path("/ws/user_y/agent_x")


def test_default_base_uses_settings(monkeypatch):
    monkeypatch.setattr(wp, "_LAYOUT", "flat")
    from xyz_agent_context.settings import settings
    p = wp.agent_workspace_path("a", "u")
    assert str(p) == str(Path(settings.base_working_path) / "a_u")


# ---------- flat → nested migration ----------

_KNOWN = {"binliang", "briefing_tester_02", "new user test", "userx", "usery", "user_weird"}


def test_parse_flat_dirname_canonical_and_disambiguation():
    parse = wp._parse_flat_dirname
    # canonical {agent}_{user}
    assert parse("agent_8ad26f8b6e9c_binliang", _KNOWN) == ("agent_8ad26f8b6e9c", "binliang")
    # user_id with underscores (still a known user)
    assert parse("agent_abc123_briefing_tester_02", _KNOWN) == ("agent_abc123", "briefing_tester_02")
    # user_id with spaces
    assert parse("agent_abc123_new user test", _KNOWN) == ("agent_abc123", "new user test")
    # legacy `_user_` infix: agent_x_user_binliang → user binliang (NOT user_binliang)
    assert parse("agent_42e4_user_binliang", _KNOWN) == ("agent_42e4", "binliang")
    # a real user literally named "user_weird" wins over the infix interpretation
    assert parse("agent_42e4_user_weird", _KNOWN) == ("agent_42e4", "user_weird")
    # unknown owner → None (never guessed)
    assert parse("agent_42e4_someone_unknown", _KNOWN) is None
    # non-flat
    assert parse("__MACOSX", _KNOWN) is None
    assert parse("binliang", _KNOWN) is None
    assert parse("agent_only", _KNOWN) is None


def _make_flat(base: Path, name: str):
    d = base / name
    (d / "skills").mkdir(parents=True)
    (d / "marker.txt").write_text(name)
    return d


def test_migrate_apply_and_idempotent(tmp_path):
    _make_flat(tmp_path, "agent_a1_userx")
    _make_flat(tmp_path, "agent_b2_userx")
    _make_flat(tmp_path, "agent_c3_usery")
    (tmp_path / "__MACOSX").mkdir()  # non-flat → skipped

    rep = wp.migrate_flat_to_nested(str(tmp_path), _KNOWN, dry_run=False)
    assert len(rep["moved"]) == 3
    assert (tmp_path / "userx" / "agent_a1" / "marker.txt").read_text() == "agent_a1_userx"
    assert (tmp_path / "userx" / "agent_b2").is_dir()
    assert (tmp_path / "usery" / "agent_c3").is_dir()
    assert not (tmp_path / "agent_a1_userx").exists()
    assert "__MACOSX" in rep["skipped"]

    # second run = no-op (already nested; user dirs don't start with agent_)
    rep2 = wp.migrate_flat_to_nested(str(tmp_path), _KNOWN, dry_run=False)
    assert rep2["moved"] == []


def test_migrate_dry_run_moves_nothing(tmp_path):
    _make_flat(tmp_path, "agent_a1_userx")
    rep = wp.migrate_flat_to_nested(str(tmp_path), _KNOWN, dry_run=True)
    assert len(rep["moved"]) == 1
    assert (tmp_path / "agent_a1_userx").is_dir()       # still flat
    assert not (tmp_path / "userx").exists()


def test_migrate_conflict_left_in_place(tmp_path):
    _make_flat(tmp_path, "agent_a1_userx")
    (tmp_path / "userx" / "agent_a1").mkdir(parents=True)  # pre-existing target
    rep = wp.migrate_flat_to_nested(str(tmp_path), _KNOWN, dry_run=False)
    assert "agent_a1_userx" in rep["conflicts"]
    assert (tmp_path / "agent_a1_userx").is_dir()          # NOT moved/clobbered


# ---------- reader fallback resolvers (flat data after nested flip) ----------

def test_resolve_existing_workspace_prefers_nested(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "_LAYOUT", "nested")
    (tmp_path / "u" / "agent_x").mkdir(parents=True)
    assert wp.resolve_existing_workspace("agent_x", "u", str(tmp_path)) == tmp_path / "u" / "agent_x"


def test_resolve_existing_workspace_falls_back_to_flat(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "_LAYOUT", "nested")
    (tmp_path / "agent_x_u").mkdir()                       # only legacy flat exists
    assert wp.resolve_existing_workspace("agent_x", "u", str(tmp_path)) == tmp_path / "agent_x_u"


def test_resolve_existing_workspace_default_when_none(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "_LAYOUT", "nested")
    assert wp.resolve_existing_workspace("agent_x", "u", str(tmp_path)) == tmp_path / "u" / "agent_x"


def test_resolve_file_direct_when_nested(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "_LAYOUT", "nested")
    f = tmp_path / "u" / "agent_x" / "work" / "o.html"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    # stored file_path already nested → direct join hits
    got = wp.resolve_workspace_relative_file("u/agent_x/work/o.html", "agent_x", "u", str(tmp_path))
    assert got == f


def test_resolve_file_flat_stored_but_dir_migrated(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "_LAYOUT", "nested")
    # file lives at the NEW nested location, but DB row still has the FLAT prefix
    f = tmp_path / "u" / "agent_x" / "work" / "o.html"
    f.parent.mkdir(parents=True)
    f.write_text("x")
    got = wp.resolve_workspace_relative_file("agent_x_u/work/o.html", "agent_x", "u", str(tmp_path))
    assert got == f


def test_resolve_file_missing_returns_direct(tmp_path, monkeypatch):
    monkeypatch.setattr(wp, "_LAYOUT", "nested")
    got = wp.resolve_workspace_relative_file("u/agent_x/nope.html", "agent_x", "u", str(tmp_path))
    assert got == tmp_path / "u/agent_x/nope.html"
