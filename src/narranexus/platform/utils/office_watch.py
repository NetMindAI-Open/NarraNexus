"""
@file_name: office_watch.py
@author: NetMind.AI
@date: 2026-07-13
@description: Shared constants + helpers for the live Office-document preview
(officecli watch). Used by the backend office-watch routes
(`backend/routes/office_watch/proxy.py`): the `/office-watch/open` endpoint
ensures a watch is running for an office artifact and the reverse-proxy streams
it to the browser.

Design: an office document registered as an artifact renders live. When its
tab is viewed, the backend `open` endpoint calls `ensure_watch`, which
ALLOCATES a dedicated port for that file (injective — never two files on one
port, so several docs can be previewed at once without cross-wiring one tab
onto another's document) and spawns a DETACHED `officecli watch` on it
(co-located with the agent's officecli edits, so it shares the resident and
live-refreshes over SSE), then returns the port so `open` can mint a signed
proxy URL. The port range is also a security allowlist — the proxy refuses to
dial anything outside it, so it can never become an SSRF into other
in-container ports (e.g. the executor :8020 or sqlite :8100).
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import threading
import time
from pathlib import Path

from loguru import logger

# Artifact kind for an office document (.pptx/.docx/.xlsx) that renders as a
# LIVE officecli-watch preview. Single source of truth — imported by the
# artifact registration impl (register/whitelist) and the office-watch proxy route.
OFFICE_LIVE_KIND = "application/vnd.officecli-live"

# Allowed officecli watch port range = the pool the allocator hands out, one
# dedicated port per concurrently-previewed file (officecli happily runs many
# watches at once, each bound to its own port — verified). The proxy allowlists
# exactly this range as its SSRF guard. officecli's own default is 26315; 20
# slots covers any realistic number of docs a single user previews at once.
WATCH_PORT_MIN = 26315
WATCH_PORT_MAX = 26334

# The argv flag that binds a watch to its port. One constant so the spawn
# (_watch_argv) and the adopt-time identity match (_cmdline_is_our_watch)
# share the SAME fact — not two hardcoded copies that can drift apart.
WATCH_PORT_FLAG = "--port"


def is_watch_port(port: int) -> bool:
    """True if ``port`` is inside the allowed officecli watch range."""
    try:
        return WATCH_PORT_MIN <= int(port) <= WATCH_PORT_MAX
    except (TypeError, ValueError):
        return False


def resolve_watch_file(agent_id: str, user_id: str, file_path: str) -> str:
    """Confine ``file_path`` to the agent workspace and confirm it exists.

    Accepts an absolute or workspace-relative path. Returns the path
    RELATIVE to the workspace root (POSIX form) on success.

    Raises:
        ValueError: if the path escapes the workspace, does not exist, is not
            a regular file, or is not a supported Office format. The message
            is actionable and surfaced straight to the agent.
    """
    from narranexus.platform.utils.workspace_paths import (
        resolve_existing_workspace,
        resolve_inside_workspace,
    )

    # Lexical root (resolve_existing_workspace realpaths only the base): the
    # workspace dir may have been swapped for a link to another tenant's.
    workspace = resolve_existing_workspace(agent_id, user_id)
    try:
        candidate = resolve_inside_workspace(workspace, file_path)
    except ValueError:
        raise ValueError(
            f"file_path is outside your agent workspace ({workspace}). Watch a file you created inside your workspace."
        ) from None
    rel = candidate.relative_to(workspace)
    if not candidate.is_file():
        raise ValueError(
            f"file_path does not point at an existing file: {file_path}. "
            f"Create the document first, then start the watch."
        )
    if candidate.suffix.lower() not in (".pptx", ".docx", ".xlsx"):
        raise ValueError(f"live preview only supports .pptx/.docx/.xlsx; got '{candidate.suffix}'.")
    return rel.as_posix()


def _port_listening(port: int, host: str = "127.0.0.1") -> bool:
    """True if something is already accepting connections on host:port."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.3)
        return s.connect_ex((host, port)) == 0


def _officecli_bin() -> str:
    """Resolve officecli to an absolute path, repairing PATH (a stripped MCP
    subprocess PATH can hide ~/.local/bin even though it works in the shell)."""
    found = shutil.which("officecli")
    if found:
        return found
    local = Path.home() / ".local" / "bin" / "officecli"
    return str(local) if local.exists() else "officecli"


# File→port assignments, owned by the process that spawns the watches (the
# backend for local/desktop, the executor for cloud — whoever calls
# ensure_watch). Keyed by ABSOLUTE path so it's globally unique across agents.
# This map gives EXACT file identity: a new file never reuses a port already
# serving a DIFFERENT file — the silent wrong-content bug of the old
# hash-to-port scheme (two files hashing to one port → the second tab rendered
# the first's document). Guarded by a lock because ensure_watch runs in a
# thread pool (run_in_executor).
_alloc_lock = threading.Lock()
_assignments: dict[str, int] = {}


def _allocate_port(abs_file: str) -> tuple[int | None, bool]:
    """Assign a watch port to ``abs_file`` (an absolute, globally-unique path).

    Returns ``(port, already_running)``. ``port`` is None only when the whole
    range is occupied by LIVE watches. The port is reserved under the lock so
    concurrent allocations for different files can't pick the same slot.

    Invariants that eliminate cross-file wrong-content:
    - Same file → its recorded port (reuse if the watch is live; respawn on the
      same slot if it idle-stopped).
    - A NEW file only ever gets a port that is BOTH unreserved AND not currently
      listening — so it can never land on a port serving another file.
    - Exhaustion self-heals: a reserved slot whose watch has died is reclaimed
      before giving up.
    """
    with _alloc_lock:
        recorded = _assignments.get(abs_file)
        if recorded is not None:
            return recorded, _port_listening(recorded)
        reserved = set(_assignments.values())
        for port in range(WATCH_PORT_MIN, WATCH_PORT_MAX + 1):
            if port not in reserved and not _port_listening(port):
                _assignments[abs_file] = port
                return port, False
        # No free slot: reclaim a reserved-but-dead one (its watch idle-stopped).
        for other, port in list(_assignments.items()):
            if not _port_listening(port):
                del _assignments[other]
                _assignments[abs_file] = port
                return port, False
        return None, False


def _release_port(abs_file: str, port: int) -> None:
    """Drop a reservation whose spawn threw (only if it's still ours)."""
    with _alloc_lock:
        if _assignments.get(abs_file) == port:
            del _assignments[abs_file]


# --- disk-is-truth reconcile (survives a backend/executor restart) -----------
# The in-memory `_assignments` map is lost on restart, but the DETACHED watches
# keep running. Without reconcile, the next open for a still-watched file sees
# an empty map, skips the (still-listening) orphan port, and spawns a SECOND
# watch — which officecli refuses (same-file single-watch), so it never comes
# up and the tab shows "could not open". A tiny sidecar file per live watch
# lets a fresh process rediscover and ADOPT the orphan instead of double-
# spawning. Sidecars are `.`-prefixed (excluded from the files API, like the
# watch log) and reaped when their watch is dead.


def _watch_meta_path(workspace: Path, port: int) -> Path:
    return workspace / f".officecli_watch_{port}.meta"


def _watch_argv(rel_file: str, port: int) -> list[str]:
    """The single source of truth for how a watch is launched. Both the spawn
    (``ensure_watch``) and the adopt-time identity check (``_cmdline_is_our_watch``)
    share the ``WATCH_PORT_FLAG`` token, so a change to the command line can't
    silently break adopt — a binding test spawns via this and matches against it."""
    return [_officecli_bin(), "watch", rel_file, WATCH_PORT_FLAG, str(port)]


def _cmdline_is_our_watch(cmd: str, port: int) -> bool:
    """True if the argv string ``cmd`` is an officecli watch bound to ``port``.

    Token-level, NOT full-argv equality: argv[0] varies (which/absolute/bare —
    see ``_officecli_bin``) and rel_file's form depends on cwd, so we only
    require the invariant shape from ``_watch_argv``: an ``officecli`` binary
    plus adjacent ``WATCH_PORT_FLAG <port>`` tokens."""
    tokens = cmd.split()
    if not any("officecli" in t for t in tokens):
        return False
    target = str(port)
    return any(
        tokens[i] == WATCH_PORT_FLAG and tokens[i + 1] == target
        for i in range(len(tokens) - 1)
    )


def _write_watch_meta(workspace: Path, port: int, abs_file: str, pid: int) -> None:
    """Record which file+pid owns a watch port (best-effort — a missing sidecar
    only costs a reconcile miss, never correctness)."""
    try:
        _watch_meta_path(workspace, port).write_text(
            json.dumps({"file": abs_file, "pid": pid, "port": port}),
            encoding="utf-8",
        )
    except OSError as e:
        logger.warning(f"officecli watch: could not write sidecar for :{port}: {e}")


def _pid_alive(pid: int) -> bool:
    """True if a process with ``pid`` exists (signal 0 probes without killing)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another uid — still alive
    return True


def _proc_cmdline(pid: int) -> str | None:
    """The argv of ``pid`` as a space-joined string, cross-platform, or None if
    unavailable. Linux reads /proc/<pid>/cmdline; elsewhere (macOS desktop)
    falls back to ``ps`` with a tight timeout so it can't wedge the caller.
    ``ps`` is resolved to an absolute path — this module exists because a
    stripped subprocess PATH hides binaries (see ``_officecli_bin``)."""
    try:
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            raw = f.read()
        if raw:
            return raw.replace(b"\x00", b" ").decode("utf-8", "replace").strip()
    except OSError:
        pass
    ps_bin = shutil.which("ps") or "/bin/ps"
    try:
        # absolute ps, pid is an int. -ww: full argv — BSD/macOS ps truncates
        # the command column to the terminal width (79 when piped, as here),
        # and our identity token (--port <port>) is at the END of the argv, so
        # a longer install path or nested file would drop it → adopt refused.
        out = subprocess.run(
            [ps_bin, "-ww", "-p", str(pid), "-o", "command="],
            capture_output=True,
            text=True,
            timeout=0.5,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    line = out.stdout.strip()
    return line or None


def _proc_identity_ok(pid: int, port: int) -> bool:
    """True only when ``pid`` is provably OUR officecli watch for ``port``.

    Wrong-content is silent, so identity must hold on EVERY platform, not just
    Linux. Evidence is the live process's command line (``_cmdline_is_our_watch``):
    a recycled pid almost never re-runs ``officecli … --port <port>``. When the
    command line is UNobtainable (no /proc, ps missing) we REFUSE (return
    False): a loud double-spawn (the front-end shows 'could not open' + Retry)
    is strictly better than adopting a port that may serve another document.

    Residual assumption: this proves the pid IS such a watch, not that the pid
    is what is currently listening on ``port`` — those are separate facts. The
    injective map guard (a port already owned is never adopted) is what closes
    that gap, so both must stay."""
    cmd = _proc_cmdline(pid)
    if cmd is None:
        return False  # no identity evidence → do not adopt
    return _cmdline_is_our_watch(cmd, port)


def _terminate_group(pid: int) -> None:
    """Kill the detached watch's process GROUP (spawned with start_new_session,
    so the child leads its own group). Best-effort."""
    try:
        os.killpg(os.getpgid(pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError, OSError) as e:
        logger.warning(f"officecli watch: could not terminate pid {pid}: {e}")


def _reconcile_from_disk(workspace: Path, abs_file: str) -> None:
    """Repopulate `_assignments[abs_file]` from a live sidecar if one exists,
    and reap DEAD sidecars along the way. Called before allocation so a restart
    reuses an orphan watch instead of double-spawning it.

    Safety (all matter — a wrong adopt renders another file's document
    silently, the module's classic wrong-content class):
    - reap ONLY when the pid is gone; a "pid alive + port not yet listening"
      sidecar is a watch still inside its start-up window (meta is written
      before the ~6s bind wait) — leaving it is correct, deleting it strands a
      healthy watch's record. (Dead-meta reaping runs regardless of which port
      it names — a readable meta on a port we already own is stale, since the
      mid-spawn meta isn't written until after Popen.)
    - adopt never STEALS a port THIS process already owns (`_assignments.values()`):
      that slot is mid-spawn, not reconcile's business;
    - adopt only a port that is (a) free in our map — the INJECTIVE guard, so
      two files never map to one port — (b) serving our file per the sidecar,
      and (c) provably the SAME officecli watch for this port (its argv per
      ``_proc_identity_ok``), so a recycled pid on a port another agent's watch
      now serves can't be mistaken for ours. No evidence → refuse. On any
      conflict, DON'T steal — fall through to normal allocation.
    """
    try:
        metas = list(workspace.glob(".officecli_watch_*.meta"))
    except OSError:
        return
    with _alloc_lock:
        owned_ports = set(_assignments.values())
    for meta_path in metas:
        try:
            data = json.loads(meta_path.read_text(encoding="utf-8"))
            port = int(data["port"])
            pid = int(data["pid"])
            owner = str(data["file"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            continue
        if not _pid_alive(pid):
            # Dead watch → drop the sidecar so its port frees up for reuse.
            meta_path.unlink(missing_ok=True)
            continue
        if port in owned_ports:
            continue  # mid-spawn by this process — not ours to reconcile
        if not _port_listening(port):
            continue  # healthy start-up window (pid alive, not yet bound)
        if owner != abs_file or not _proc_identity_ok(pid, port):
            continue  # another file, or a recycled pid — never adopt
        with _alloc_lock:
            if port in set(_assignments.values()):
                continue  # lost a race for this port — don't double-map
            _assignments[abs_file] = port
        return


def ensure_watch(agent_id: str, user_id: str, rel_file: str, wait_s: float = 6.0) -> int | None:
    """Ensure an `officecli watch` server is running for ``rel_file`` and return
    the port ALLOCATED to it (None on failure / range exhaustion).

    Allocates a dedicated port per file (see ``_allocate_port``) instead of
    hashing, so previewing several documents at once can never make one file's
    tab render another file's document. Reuses the running watch if this file
    already has one.

    Spawns the watch **detached** (``start_new_session=True``) so it survives
    the caller — the fix for the agent-backgrounded (`&`) watch dying when its
    bash tool call returns. Because it runs on the same host/container as the
    agent's officecli edits, it shares officecli's resident and live-refreshes
    over SSE as the agent edits.

    Valid only when the caller is co-located with the workspace + the agent's
    officecli (local/desktop, or inside the executor container for cloud).
    """
    from narranexus.platform.utils.workspace_paths import (
        resolve_existing_workspace,
        resolve_inside_workspace,
    )

    workspace = resolve_existing_workspace(agent_id, user_id)
    # Re-confine here too: the executor's /watch/ensure hands rel_file straight
    # in, and a symlinked workspace would otherwise spawn a watch (and write its
    # log/meta sidecars) inside another tenant's tree.
    try:
        abs_file = str(resolve_inside_workspace(workspace, rel_file))
    except ValueError as e:
        logger.warning(f"officecli watch refused: {e}")
        return None

    # Disk-is-truth: after a restart the in-memory map is empty but the detached
    # watch may still be live. Adopt it (and reap dead sidecars) BEFORE
    # allocating, so we reuse the orphan instead of spawning a doomed 2nd watch.
    if abs_file not in _assignments:
        _reconcile_from_disk(workspace, abs_file)

    port, already_running = _allocate_port(abs_file)
    if port is None:
        logger.warning("officecli watch: no free port in range; too many live previews at once")
        return None
    if already_running:
        return port

    env = dict(os.environ)
    extra_path = str(Path.home() / ".local" / "bin")
    if extra_path not in env.get("PATH", ""):
        env["PATH"] = f"{extra_path}:{env.get('PATH', '')}"

    log_path = workspace / f".officecli_watch_{port}.log"
    try:
        with open(log_path, "ab") as log:
            proc = subprocess.Popen(  # workspace-confined file + allowlisted port
                _watch_argv(rel_file, port),
                cwd=str(workspace),
                env=env,
                stdout=log,
                stderr=log,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # detach: survive the tool call / parent exit
            )
    except Exception as e:  # spawn errors surface as None, never crash the caller
        logger.warning(f"failed to spawn officecli watch on :{port}: {e}")
        _release_port(abs_file, port)
        return None

    # Record the owner so a later process (restart) can adopt this watch instead
    # of double-spawning it.
    _write_watch_meta(workspace, port, abs_file, proc.pid)

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if _port_listening(port):
            return port
        time.sleep(0.25)
    # Never came up: KILL the process we spawned before releasing the port —
    # a bare release left it as an orphan that could come up later on a slot
    # the allocator now believes is free (wrong-content / wedged-port class).
    logger.warning(f"officecli watch on :{port} did not come up within {wait_s}s")
    _terminate_group(proc.pid)
    _watch_meta_path(workspace, port).unlink(missing_ok=True)
    _release_port(abs_file, port)
    return None
