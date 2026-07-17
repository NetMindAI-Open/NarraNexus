"""
@file_name: test_office_watch.py
@author: NetMind.AI
@date: 2026-07-13
@description: Tests for the office-watch shared helpers — the port allowlist
(SSRF guard for the reverse-proxy) and workspace-confinement of the file the
agent asks to preview.
"""

from __future__ import annotations

import pytest

from xyz_agent_context.settings import settings
from xyz_agent_context.utils.office_watch import (
    WATCH_PORT_MAX,
    WATCH_PORT_MIN,
    is_watch_port,
    resolve_watch_file,
)


def test_is_watch_port_range():
    assert is_watch_port(WATCH_PORT_MIN)
    assert is_watch_port(WATCH_PORT_MAX)
    assert not is_watch_port(WATCH_PORT_MIN - 1)
    assert not is_watch_port(WATCH_PORT_MAX + 1)
    # Ports the proxy must never dial (executor / sqlite proxy / backend).
    assert not is_watch_port(8020)
    assert not is_watch_port(8100)
    assert not is_watch_port(8000)
    assert not is_watch_port("nope")  # type: ignore[arg-type]


def _ws(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path))
    from xyz_agent_context.utils.workspace_paths import agent_workspace_path

    ws = agent_workspace_path("a1", "u1")
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def test_resolve_watch_file_ok(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch)
    (ws / "deck.pptx").write_bytes(b"x")
    assert resolve_watch_file("a1", "u1", "deck.pptx") == "deck.pptx"
    # Absolute path inside the workspace also resolves.
    assert resolve_watch_file("a1", "u1", str(ws / "deck.pptx")) == "deck.pptx"


def test_resolve_watch_file_missing(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="existing file"):
        resolve_watch_file("a1", "u1", "nope.pptx")


def test_resolve_watch_file_escape(tmp_path, monkeypatch):
    _ws(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="outside your agent workspace"):
        resolve_watch_file("a1", "u1", "../../etc/passwd")


def test_resolve_watch_file_bad_ext(tmp_path, monkeypatch):
    ws = _ws(tmp_path, monkeypatch)
    (ws / "notes.txt").write_bytes(b"x")
    with pytest.raises(ValueError, match="only supports"):
        resolve_watch_file("a1", "u1", "notes.txt")


# --- port allocator: the fix for concurrent multi-doc preview ----------------
# _allocate_port is pure given _port_listening; we simulate which ports are up.


def _fresh_alloc(monkeypatch, listening: set[int]):
    """Reset the allocator table + stub _port_listening from ``listening``."""
    import xyz_agent_context.utils.office_watch as ow

    monkeypatch.setattr(ow, "_assignments", {})
    monkeypatch.setattr(ow, "_port_listening", lambda p, host="127.0.0.1": p in listening)
    return ow


def test_allocate_port_injective_and_reuse(monkeypatch):
    listening: set[int] = set()
    ow = _fresh_alloc(monkeypatch, listening)

    p1, run1 = ow._allocate_port("/ws/a.pptx")
    assert p1 == ow.WATCH_PORT_MIN and run1 is False
    # Same file → same port (respawn, watch not yet listening).
    assert ow._allocate_port("/ws/a.pptx") == (p1, False)
    # Different file → a DIFFERENT port. This is the whole point of the fix.
    p2, _ = ow._allocate_port("/ws/b.pptx")
    assert p2 != p1
    # Once a.pptx's watch is up, the same file reuses it as already-running.
    listening.add(p1)
    assert ow._allocate_port("/ws/a.pptx") == (p1, True)


def test_allocate_port_never_reuses_a_live_other_files_port(monkeypatch):
    # Some other watch already occupies the first slot.
    ow = _fresh_alloc(monkeypatch, {26315})
    # A brand-new file MUST skip the occupied port, never render its document.
    port, running = ow._allocate_port("/ws/new.pptx")
    assert port != 26315 and running is False


def test_allocate_port_exhaustion_then_dead_reclaim(monkeypatch):
    import xyz_agent_context.utils.office_watch as ow

    span = ow.WATCH_PORT_MAX - ow.WATCH_PORT_MIN + 1
    assignments = {f"/ws/f{i}.pptx": ow.WATCH_PORT_MIN + i for i in range(span)}
    live = set(assignments.values())
    monkeypatch.setattr(ow, "_assignments", assignments)
    monkeypatch.setattr(ow, "_port_listening", lambda p, host="127.0.0.1": p in live)

    # Every slot busy with a LIVE watch → a new file can't be placed.
    assert ow._allocate_port("/ws/extra.pptx") == (None, False)
    # One watch idle-stops → its slot is reclaimed for the new file.
    live.discard(ow.WATCH_PORT_MIN)
    port, running = ow._allocate_port("/ws/extra.pptx")
    assert port == ow.WATCH_PORT_MIN and running is False
