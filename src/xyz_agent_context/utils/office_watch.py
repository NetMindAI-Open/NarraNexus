"""
@file_name: office_watch.py
@author: NetMind.AI
@date: 2026-07-13
@description: Shared constants + helpers for the live Office-document preview
(officecli watch). Used by the backend office-watch routes
(`backend/routes/office_watch_proxy.py`): the `/office-watch/open` endpoint
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

import os
import shutil
import socket
import subprocess
import threading
import time
from pathlib import Path

from loguru import logger

# Artifact kind for an office document (.pptx/.docx/.xlsx) that renders as a
# LIVE officecli-watch preview. Single source of truth — imported by
# artifact_runner (register/whitelist) and the office-watch proxy route.
OFFICE_LIVE_KIND = "application/vnd.officecli-live"

# Allowed officecli watch port range = the pool the allocator hands out, one
# dedicated port per concurrently-previewed file (officecli happily runs many
# watches at once, each bound to its own port — verified). The proxy allowlists
# exactly this range as its SSRF guard. officecli's own default is 26315; 20
# slots covers any realistic number of docs a single user previews at once.
WATCH_PORT_MIN = 26315
WATCH_PORT_MAX = 26334


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
    from xyz_agent_context.utils.workspace_paths import resolve_existing_workspace

    workspace = resolve_existing_workspace(agent_id, user_id).resolve()

    raw = Path(file_path)
    candidate = (raw if raw.is_absolute() else workspace / raw).resolve()

    try:
        rel = candidate.relative_to(workspace)
    except ValueError:
        raise ValueError(
            f"file_path is outside your agent workspace ({workspace}). Watch a file you created inside your workspace."
        )
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
    from xyz_agent_context.utils.workspace_paths import resolve_existing_workspace

    workspace = resolve_existing_workspace(agent_id, user_id)
    abs_file = str((workspace / rel_file).resolve())

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
            subprocess.Popen(  # noqa: S603 — workspace-confined file + allowlisted port
                [_officecli_bin(), "watch", rel_file, "--port", str(port)],
                cwd=str(workspace),
                env=env,
                stdout=log,
                stderr=log,
                stdin=subprocess.DEVNULL,
                start_new_session=True,  # detach: survive the tool call / parent exit
            )
    except Exception as e:  # noqa: BLE001
        logger.warning(f"failed to spawn officecli watch on :{port}: {e}")
        _release_port(abs_file, port)
        return None

    deadline = time.monotonic() + wait_s
    while time.monotonic() < deadline:
        if _port_listening(port):
            return port
        time.sleep(0.25)
    # Slow to come up — the port may be wedged by a still-dying watch. Release
    # the reservation so the NEXT open allocates a fresh port instead of retrying
    # the same stuck slot; this is what makes restart-after-idle-death reliable
    # (the "Could not open the live preview" symptom when a watch idle-died and
    # its port hadn't fully released).
    logger.warning(f"officecli watch on :{port} did not come up within {wait_s}s")
    _release_port(abs_file, port)
    return None
