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

from narranexus.platform.settings import settings
from narranexus.platform.utils.office_watch import (
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
    from narranexus.platform.utils.workspace_paths import agent_workspace_path

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
    import narranexus.platform.utils.office_watch as ow

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
    import narranexus.platform.utils.office_watch as ow

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


# --- lifecycle robustness: disk reconcile + kill-orphan (fix: docx won't load
#     after a backend restart spawned a doomed 2nd watch for the same file) ----


def test_reconcile_adopts_live_orphan_instead_of_double_spawning(tmp_path, monkeypatch):
    """After a restart the in-memory map is empty but a detached watch is still
    listening for the file. ensure_watch must ADOPT it, never spawn a second
    watch (officecli is same-file single-watch; the 2nd would fail to come up
    and the tab would show 'could not open')."""
    import narranexus.platform.utils.office_watch as ow

    ws = _ws(tmp_path, monkeypatch)
    (ws / "deck.pptx").write_bytes(b"x")
    abs_file = str((ws / "deck.pptx").resolve())
    ow._write_watch_meta(ws, ow.WATCH_PORT_MIN, abs_file, 4242)

    monkeypatch.setattr(ow, "_assignments", {})
    monkeypatch.setattr(ow, "_port_listening", lambda p, host="127.0.0.1": p == ow.WATCH_PORT_MIN)
    monkeypatch.setattr(ow, "_pid_alive", lambda pid: pid == 4242)
    # Identity evidence: the live process IS our officecli watch on this port.
    monkeypatch.setattr(
        ow, "_proc_cmdline", lambda pid: f"/x/officecli watch deck.pptx --port {ow.WATCH_PORT_MIN}"
    )
    spawned = {"popen": False}
    monkeypatch.setattr(
        ow.subprocess, "Popen", lambda *a, **k: spawned.__setitem__("popen", True)
    )

    port = ow.ensure_watch("a1", "u1", "deck.pptx")
    assert port == ow.WATCH_PORT_MIN
    assert spawned["popen"] is False  # adopted the orphan, did not respawn


def test_reconcile_cleans_a_dead_watchs_meta(tmp_path, monkeypatch):
    """A sidecar whose watch died (pid gone) is removed, not adopted — so the
    port frees up for a fresh allocation. Reap is driven ONLY by pid death now
    (port-listening=True here proves that is not what triggers the reap)."""
    import narranexus.platform.utils.office_watch as ow

    ws = _ws(tmp_path, monkeypatch)
    abs_file = str((ws / "deck.pptx").resolve())
    ow._write_watch_meta(ws, 26320, abs_file, 9999)
    monkeypatch.setattr(ow, "_pid_alive", lambda pid: False)
    monkeypatch.setattr(ow, "_port_listening", lambda p, host="127.0.0.1": True)

    monkeypatch.setattr(ow, "_assignments", {})
    ow._reconcile_from_disk(ws, abs_file)
    assert not (ws / ".officecli_watch_26320.meta").exists()
    assert abs_file not in ow._assignments


def test_reconcile_leaves_a_starting_watchs_meta_alone(tmp_path, monkeypatch):
    """A watch inside its start-up window (pid alive, port not yet listening —
    the meta is written before the bind wait) must NOT be reaped: deleting it
    would strand a healthy watch's record for the next restart."""
    import narranexus.platform.utils.office_watch as ow

    ws = _ws(tmp_path, monkeypatch)
    abs_file = str((ws / "deck.pptx").resolve())
    ow._write_watch_meta(ws, 26321, abs_file, 4242)
    monkeypatch.setattr(ow, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(ow, "_port_listening", lambda p, host="127.0.0.1": False)

    monkeypatch.setattr(ow, "_assignments", {})
    ow._reconcile_from_disk(ws, abs_file)
    assert (ws / ".officecli_watch_26321.meta").exists()  # left alone
    assert abs_file not in ow._assignments  # not adopted (not listening yet)


def test_reconcile_refuses_to_adopt_a_port_owned_by_another_file(tmp_path, monkeypatch):
    """Injective guard: a live sidecar naming our file on a port ANOTHER file
    already holds in the map must NOT be adopted (that would render the other
    file's document in our tab — the classic wrong-content bug). Fall through
    to normal allocation instead."""
    import narranexus.platform.utils.office_watch as ow

    ws = _ws(tmp_path, monkeypatch)
    abs_file = str((ws / "deck.pptx").resolve())
    ow._write_watch_meta(ws, ow.WATCH_PORT_MIN, abs_file, 4242)
    monkeypatch.setattr(ow, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(ow, "_port_listening", lambda p, host="127.0.0.1": True)
    # Another file already owns WATCH_PORT_MIN in the live map.
    monkeypatch.setattr(ow, "_assignments", {"/ws/other.pptx": ow.WATCH_PORT_MIN})

    ow._reconcile_from_disk(ws, abs_file)
    assert abs_file not in ow._assignments  # refused — no double-map
    assert ow._assignments["/ws/other.pptx"] == ow.WATCH_PORT_MIN  # untouched


def test_reconcile_refuses_a_recycled_pid_with_mismatched_cmdline(tmp_path, monkeypatch):
    """Identity guard: the sidecar's pid is alive but its command line is not
    our watch (the pid number was recycled into a different process after a
    restart) — refuse to adopt even though pid+port look live."""
    import narranexus.platform.utils.office_watch as ow

    ws = _ws(tmp_path, monkeypatch)
    abs_file = str((ws / "deck.pptx").resolve())
    # An old-format sidecar still carrying a stray `start` key — proves it
    # still parses (reconcile reads only file/pid/port).
    (ws / ".officecli_watch_26322.meta").write_text(
        '{"file": "%s", "pid": 4242, "port": 26322, "start": "111"}' % abs_file
    )
    monkeypatch.setattr(ow, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(ow, "_port_listening", lambda p, host="127.0.0.1": True)
    # pid 4242 is alive but was recycled into some OTHER process — its cmdline
    # is not our officecli watch, so identity fails cross-platform.
    monkeypatch.setattr(ow, "_proc_cmdline", lambda pid: "/usr/bin/python some_script.py")
    monkeypatch.setattr(ow, "_assignments", {})

    ow._reconcile_from_disk(ws, abs_file)
    assert abs_file not in ow._assignments  # identity mismatch → not adopted


def test_reconcile_refuses_to_adopt_without_identity_evidence(tmp_path, monkeypatch):
    """macOS/desktop regression: when NO identity evidence is obtainable
    (the command line is unreadable), reconcile must NOT adopt — a loud
    double-spawn (front-end shows 'could not open' + Retry) beats silently
    rendering another document. (Historically the check was a Linux-only
    start-time compare, a no-op on desktop — this is that hole, now closed
    by the cmdline evidence.)"""
    import narranexus.platform.utils.office_watch as ow

    ws = _ws(tmp_path, monkeypatch)
    abs_file = str((ws / "deck.pptx").resolve())
    # Old-format sidecar (stray `start` key) with no cmdline available —
    # forward-compat parsing AND refuse-on-no-evidence in one.
    (ws / ".officecli_watch_26323.meta").write_text(
        '{"file": "%s", "pid": 4242, "port": 26323, "start": null}' % abs_file
    )
    monkeypatch.setattr(ow, "_pid_alive", lambda pid: True)  # recycled pid looks alive
    monkeypatch.setattr(ow, "_port_listening", lambda p, host="127.0.0.1": True)
    monkeypatch.setattr(ow, "_proc_cmdline", lambda pid: None)  # no /proc, ps unavailable
    monkeypatch.setattr(ow, "_assignments", {})  # empty — the very moment reconcile runs

    ow._reconcile_from_disk(ws, abs_file)
    assert abs_file not in ow._assignments  # no evidence → refuse to adopt


def test_slow_start_kills_the_orphan_and_returns_none(tmp_path, monkeypatch):
    """A watch that never comes up within wait_s is terminated — not left
    listening later on a port the allocator now believes is free."""
    import narranexus.platform.utils.office_watch as ow

    ws = _ws(tmp_path, monkeypatch)
    (ws / "deck.pptx").write_bytes(b"x")
    killed = {"pid": None}

    class _Proc:
        pid = 5555

    monkeypatch.setattr(ow.subprocess, "Popen", lambda *a, **k: _Proc())
    monkeypatch.setattr(ow, "_port_listening", lambda p, host="127.0.0.1": False)
    monkeypatch.setattr(ow, "_terminate_group", lambda pid: killed.__setitem__("pid", pid))
    monkeypatch.setattr(ow, "_assignments", {})

    port = ow.ensure_watch("a1", "u1", "deck.pptx", wait_s=0.01)
    assert port is None
    assert killed["pid"] == 5555  # orphan reaped, not leaked
    # Its sidecar is removed too, so no stale meta survives the failed spawn.
    assert not list(ws.glob(".officecli_watch_*.meta"))


def test_identity_recognizes_the_real_spawn_argv(tmp_path, monkeypatch):
    """The adopt-time matcher and the spawn command line must agree — argv is
    a single fact (`_watch_argv`), so a change to how a watch is launched can
    never silently disable adopt (which would regress the restart double-spawn
    bug while every reconcile test stayed green). Capture the ACTUAL argv passed
    to Popen and assert the identity matcher accepts it."""
    import narranexus.platform.utils.office_watch as ow

    ws = _ws(tmp_path, monkeypatch)
    (ws / "deck.pptx").write_bytes(b"x")
    captured: dict[str, list[str]] = {}

    class _Proc:
        pid = 7777

    def _fake_popen(argv, *a, **k):
        captured["argv"] = list(argv)
        return _Proc()

    monkeypatch.setattr(ow.subprocess, "Popen", _fake_popen)
    # Free before spawn (so a port can be allocated), listening after (so the
    # bind-wait returns the port).
    monkeypatch.setattr(ow, "_port_listening", lambda p, host="127.0.0.1": "argv" in captured)
    monkeypatch.setattr(ow, "_assignments", {})

    port = ow.ensure_watch("a1", "u1", "deck.pptx")
    assert port is not None
    # The matcher recognizes the real spawn argv on its port, and rejects it on
    # any other port — the two ends are bound through _watch_argv.
    joined = " ".join(captured["argv"])
    assert ow._cmdline_is_our_watch(joined, port)
    assert not ow._cmdline_is_our_watch(joined, port + 1)


def test_proc_cmdline_returns_full_argv_including_tail_token():
    # Real _proc_cmdline (not monkeypatched): must return the WHOLE argv incl.
    # the tail — our identity token (--port <port>) is the LAST pair, and a
    # truncated result would silently refuse adopt. Covers /proc on Linux CI.
    import subprocess
    import sys

    import narranexus.platform.utils.office_watch as ow

    sentinel = "ZZ_office_watch_tail_ZZ"
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(3)", *["pad"] * 8, sentinel]
    )
    try:
        cmd = ow._proc_cmdline(proc.pid)
        assert cmd is not None and sentinel in cmd  # tail survived, no truncation
    finally:
        proc.terminate()
        proc.wait(timeout=2)


def test_proc_cmdline_ps_branch_keeps_tail(monkeypatch):
    # Force the `ps` fallback (as on macOS, no /proc) and assert it keeps the
    # tail — the `-ww` guard against BSD ps's 79-column truncation.
    import subprocess
    import sys

    import narranexus.platform.utils.office_watch as ow

    real_open = open

    def _no_proc(path, *a, **k):
        if isinstance(path, str) and path.startswith("/proc/"):
            raise FileNotFoundError(path)
        return real_open(path, *a, **k)

    monkeypatch.setattr("builtins.open", _no_proc)

    sentinel = "ZZ_ps_tail_ZZ"
    proc = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(3)", *["pad"] * 8, sentinel]
    )
    try:
        cmd = ow._proc_cmdline(proc.pid)
        # `ps` may be absent in a sandbox → None (refuse adopt) is acceptable;
        # when present it MUST include the tail (the -ww fix).
        if cmd is not None:
            assert sentinel in cmd
    finally:
        proc.terminate()
        proc.wait(timeout=2)


# --- symlinked workspace (2026-10-28) ----------------------------------------


def _swap_ws_for_victim_link(tmp_path, monkeypatch):
    import os
    import shutil

    ws = _ws(tmp_path, monkeypatch)
    victim = tmp_path / "u2" / "a2"
    victim.mkdir(parents=True)
    (victim / "deck.pptx").write_bytes(b"victim")
    shutil.rmtree(ws)
    os.symlink(victim, ws)
    return ws, victim


def test_resolve_watch_file_refuses_a_symlinked_workspace(tmp_path, monkeypatch):
    ws, victim = _swap_ws_for_victim_link(tmp_path, monkeypatch)
    with pytest.raises(ValueError):
        resolve_watch_file("a1", "u1", "deck.pptx")
    with pytest.raises(ValueError):
        resolve_watch_file("a1", "u1", str(victim / "deck.pptx"))


def test_ensure_watch_refuses_a_symlinked_workspace(tmp_path, monkeypatch):
    import narranexus.platform.utils.office_watch as ow

    _swap_ws_for_victim_link(tmp_path, monkeypatch)
    spawned = []
    monkeypatch.setattr(ow.subprocess, "Popen", lambda *a, **k: spawned.append(a))
    monkeypatch.setattr(ow, "_port_listening", lambda p, host="127.0.0.1": False)
    monkeypatch.setattr(ow, "_assignments", {})
    assert ow.ensure_watch("a1", "u1", "deck.pptx", wait_s=0.01) is None
    assert spawned == []
    assert ow._assignments == {}
