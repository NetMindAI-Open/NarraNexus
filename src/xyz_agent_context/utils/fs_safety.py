"""
@file_name: fs_safety.py
@author: Bin Liang
@date: 2026-05-22
@description: Filesystem-safety helpers shared by logging setup and the SQLite
backend.

A stale or foreign ``~/.narranexus`` (created by root on an earlier run, or
carried over from another Mac by Migration Assistant with that machine's numeric
uid) is unwritable for the current account. That used to silently kill the DB /
logging on startup → the desktop app could only show "Connection failed".

These helpers prefer **fixing the real directory** (chmod-repair when WE own it)
over working around it, and make the unfixable (foreign-owned) case explicit so
callers can surface an actionable ``sudo chown`` instead of a cryptic OSError.
"""
from __future__ import annotations

import os
from pathlib import Path


def probe_writable(d: Path) -> bool:
    """True iff we can actually create a file in ``d``.

    ``mkdir(exist_ok=True)`` succeeds silently on an existing-but-unwritable
    dir, so a real touch/unlink is the only reliable check.
    """
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / ".write_test"
        probe.touch()
        probe.unlink()
        return True
    except (PermissionError, OSError):
        return False


def chmod_repair_owned(target: Path) -> bool:
    """Best-effort: for ``target`` and each existing ancestor under ``$HOME``
    that WE OWN but cannot write, add ``u+rwx``.

    This is the "fix it correctly" path — it repairs a dir whose perms are too
    tight (e.g. a previous run left it ``0500``). It deliberately does NOTHING to
    dirs owned by another uid (root / a foreign uid from Migration Assistant) —
    those genuinely need ``sudo chown`` and we must not pretend otherwise. Stays
    within ``$HOME`` so it never touches ``/`` or ``/Users``.

    Returns True if it changed anything (caller should retry the probe).
    """
    if not hasattr(os, "geteuid"):
        return False  # non-POSIX (Windows): no ownership model here
    uid = os.geteuid()
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        return False
    changed = False
    p = target
    while p != p.parent and (p == home or home in p.parents):
        if p.exists():
            try:
                st = p.stat()
                if st.st_uid == uid and not os.access(p, os.W_OK):
                    os.chmod(p, (st.st_mode & 0o7777) | 0o700)
                    changed = True
            except OSError:
                pass
        p = p.parent
    return changed


def ensure_writable_dir(d: Path) -> bool:
    """Make ``d`` exist and be writable, self-repairing an owned-but-tight dir.

    Returns True iff usable. False means foreign ownership / a read-only mount —
    the caller must surface that (it cannot be fixed without elevated privileges).
    """
    if probe_writable(d):
        return True
    if chmod_repair_owned(d):
        return probe_writable(d)
    return False


def narra_root_of(path: Path) -> Path:
    """The ``~/.narranexus`` ancestor of ``path`` (for the chown hint), or
    ``path`` itself if none is found."""
    for anc in [path, *path.parents]:
        if anc.name == ".narranexus":
            return anc
    return path


def chown_hint(path: Path) -> str:
    """The exact command a user should run to fix foreign ownership."""
    user = os.environ.get("USER") or os.environ.get("LOGNAME") or "$(whoami)"
    return f"sudo chown -R {user} {narra_root_of(path)}"
