"""
@file_name: run_registry.py
@author: Bin Liang
@date: 2026-08-21
@description: RunRegistry — the per-process index of live runs by
(agent_id, surface), the routing brain for live steering.

A producer that has a new message for an agent asks "is this agent already
running on THIS surface?". If yes, the message is steered into that run
(appended to its context at the next step boundary) instead of dispatching
a fresh turn. Surface-scoping is the whole point: one agent may have several
concurrent runs — multiple team rooms, a web chat, a job — and a message for
one surface must NEVER route into a run on a different surface (that would
splice a team-A message into a team-B turn's context).

Why per-process, in-memory, not a DB table: a run's steer handle (the live
push into its loop) only exists in the process that owns the run, and for the
v1 producers the producer is co-located with its target run — a team message
and the team run are both in the bus-trigger process; an owner-chat
interjection and the web run are both in the backend process. So the process
that produces is the process that holds the run. This is a seam (iron rule
#20, like ``get_admission_controller``): the interface stays the same if a
future cross-process producer needs the truth moved behind Redis / a DB view.

``run_id`` is opaque here and to ``steer_inbox``: whoever registers a run
mints the handle (it need not be the late-bound ``events.event_id``), and the
registry never interprets it.
"""

from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from typing import Any, Callable, Dict, Iterator, Optional, Tuple

from loguru import logger


@dataclass(frozen=True)
class RunHandle:
    """One live run's routing entry. ``steer`` is the opaque push handle the
    owner registered (e.g. a channel that writes into the loop's inlet); the
    registry stores and returns it without interpreting it.

    ``is_alive`` is an optional SYNCHRONOUS liveness probe: when present,
    ``live_run`` calls it and treats a dead run as no live run (and cleans the
    stale mapping), so a crashed run that never released degrades to "start a
    fresh turn" rather than "this surface is deaf forever". None = assume alive
    (the scoped ``registered`` API is the primary guard against a missed
    release; this is defence in depth)."""

    run_id: str
    agent_id: str
    surface_key: str
    steer: Any
    is_alive: Optional[Callable[[], bool]] = None


class RunRegistry:
    """In-memory, single-event-loop. Methods are synchronous dict ops with no
    await between read and write, so they are atomic on one event loop; the
    seam can move behind a lock / Redis if a thread or replica ever shares it.
    """

    def __init__(self) -> None:
        self._by_run: Dict[str, RunHandle] = {}
        self._by_surface: Dict[Tuple[str, str], str] = {}

    def register(
        self, agent_id: str, surface_key: str, run_id: str, steer: Any,
        is_alive: Optional[Callable[[], bool]] = None,
    ) -> None:
        """Record a live run. A surface holds at most one live run — a repeat
        register on the same ``(agent, surface)`` supersedes the older one:
        the superseded run's ``_by_run`` entry is dropped (else it leaks a
        SteerChannel for the life of the process), and its late ``release``
        then finds the surface no longer points at it.

        Prefer ``registered`` (the scoped API) so release can never be missed;
        this raw method exists for callers with their own scope guard."""
        key = (agent_id, surface_key)
        prev = self._by_surface.get(key)
        if prev is not None and prev != run_id:
            self._by_run.pop(prev, None)  # supersede — do not leak the old run
        self._by_run[run_id] = RunHandle(
            run_id=run_id, agent_id=agent_id, surface_key=surface_key,
            steer=steer, is_alive=is_alive,
        )
        self._by_surface[key] = run_id
        # INFO, not DEBUG: this is a per-RUN lifecycle event (not per-message —
        # live_run stays quiet), and the default log level is INFO, so DEBUG
        # would never land in dev/prod and the release/sweep lines would have no
        # matching "when did this run start" record (incident lesson #5).
        logger.info(f"[run-registry] register {run_id} on {key}")

    @contextmanager
    def registered(
        self, agent_id: str, surface_key: str, run_id: str, steer: Any,
        is_alive: Optional[Callable[[], bool]] = None,
    ) -> Iterator[None]:
        """Scoped register/release — releases in ``finally`` however the body
        exits, so a caller cannot leave a dead run pinned to a surface."""
        self.register(agent_id, surface_key, run_id, steer, is_alive)
        try:
            yield
        finally:
            self.release(run_id)

    def live_run(self, agent_id: str, surface_key: str) -> Optional[RunHandle]:
        """The live run for ``(agent, surface)``, or None. A match on a
        different surface or a different agent is impossible by construction —
        the key is the pair. A handle whose ``is_alive`` says the run is gone
        is swept here so a crashed run cannot make the surface permanently
        deaf."""
        run_id = self._by_surface.get((agent_id, surface_key))
        if run_id is None:
            return None
        handle = self._by_run.get(run_id)
        if handle is None:
            # Dangling surface entry: the surface points at a run_id no longer in
            # _by_run (e.g. one run_id was registered on two surfaces, then
            # released — release clears only its own surface_key). Behaviour is
            # already correct (no live run), but heal the stale mapping so the
            # self-sweeping invariant is complete, not half.
            del self._by_surface[(agent_id, surface_key)]
            return None
        if handle.is_alive is not None and not handle.is_alive():
            logger.info(f"[run-registry] sweeping dead run {run_id} on {(agent_id, surface_key)}")
            self.release(run_id)
            return None
        return handle

    def live_run_count(self) -> int:
        """How many runs are currently held. A tiny public window onto the
        registry's size so a leak (a superseded run left pinned) is observable
        without reaching into private fields — the seam may move behind Redis /
        a DB view, at which point ``_by_run`` stops being the truth."""
        return len(self._by_run)

    def release(self, run_id: str) -> None:
        """Drop a finished run. A run_id lives in ``_by_run`` only while its
        surface still points at it — a superseded run is popped from
        ``_by_run`` at supersede time (see ``register``), so a late release of
        a superseded run early-returns here on the ``None`` handle and never
        reaches the surface mapping. Once we hold a live handle the surface
        entry is ours to clear unconditionally; no ``== run_id`` guard is
        needed (it would always be true)."""
        handle = self._by_run.pop(run_id, None)
        if handle is None:
            return
        key = (handle.agent_id, handle.surface_key)
        self._by_surface.pop(key, None)
        logger.info(f"[run-registry] release {run_id} on {key}")  # per-run lifecycle; see register


_registry: Optional[RunRegistry] = None


def get_run_registry() -> RunRegistry:
    """The process-singleton registry (the seam, like ``get_admission_controller``).
    One per process naturally partitions by owner: the bus-trigger process holds
    its bus runs, the backend process holds its web runs."""
    global _registry
    if _registry is None:
        _registry = RunRegistry()
    return _registry


__all__ = ["RunRegistry", "RunHandle", "get_run_registry"]
