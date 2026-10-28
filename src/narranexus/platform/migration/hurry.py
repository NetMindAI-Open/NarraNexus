"""
@file_name: hurry.py
@author: NetMind.AI
@date: 2026-09-03
@description: "Stop waiting for this import" — a tiny in-process registry the
running apply consults between sessions.

Why it exists: ``apply_plan`` summarizes each imported session with ONE
helper-LLM call, serially (see applier.py's narrative loop). A 12-session
project is therefore 12 sequential model calls, which on a slow provider is
minutes. The frontend's "stop" used to mean "finish the current project, then
stop" — correct (never cut a write in half) but it could leave the user staring
at a spinner for an unbounded time, which is exactly the wait the Owner objected
to (2026-09-03).

So "stop" now means: **degrade the row that is already running**. The applier
checks this registry before each remaining session; once the id is marked, the
rest of that project takes the deterministic fallback summary
(``_summarize_session``'s existing no-LLM path: title + raw source), so the
write completes in the time of a few DB inserts instead of N model calls.
Nothing is aborted and nothing is left half-written — the agent still gets every
session, its narratives are just plainly summarized.

Scope and honesty about it:

- **In-process, best-effort.** The mark has to reach the worker running that
  apply. Local/desktop is single-process, so it always does. In a multi-worker
  cloud deploy it may land elsewhere, in which case the import simply keeps its
  LLM summaries and the user waits as before — degraded speed, never degraded
  data. (Same caveat, and same pre-flip TODO, as the netmind provisioner's
  in-process lock.) Cloud has no filesystem to import from anyway, so today this
  code only ever runs local.
- **Ids are bounded.** Every mark is dropped by ``clear`` in apply's `finally`,
  and a hard cap protects against a client that marks ids it never applies.
"""

from __future__ import annotations

from collections import OrderedDict

from loguru import logger

# Insertion-ordered so the cap can evict the oldest. Values are unused; this is
# a set with an eviction order.
_hurried: "OrderedDict[str, None]" = OrderedDict()

# A client that marks ids without ever applying them must not grow this forever.
# 256 is far above any real import batch (the UI marks at most one per stop).
_MAX_TRACKED = 256


def mark(import_id: str) -> None:
    """Ask the apply identified by ``import_id`` to stop summarizing."""
    if not import_id:
        return
    _hurried[import_id] = None
    _hurried.move_to_end(import_id)
    while len(_hurried) > _MAX_TRACKED:
        evicted, _ = _hurried.popitem(last=False)
        logger.warning(f"[migrate.hurry] evicted stale id {evicted}")


def is_hurried(import_id: str | None) -> bool:
    """True once the user has asked this import to finish fast."""
    return bool(import_id) and import_id in _hurried


def clear(import_id: str | None) -> None:
    """Drop the mark — called when the apply finishes, however it finishes."""
    if import_id:
        _hurried.pop(import_id, None)
