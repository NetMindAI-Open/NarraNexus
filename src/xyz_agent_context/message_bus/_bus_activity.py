"""
@file_name: _bus_activity.py
@author: NarraNexus
@date: 2026-07-22
@description: Lightweight live-activity status for team-room agent runs.

MessageBusTrigger runs a team-room agent in the background — the team chat UI
has no live stream to it (unlike the WS single-agent path). This module mirrors
"what is this agent doing right now" into the ``bus_agent_activity`` table so the
team-chat GET can show running / phase / elapsed / a per-turn step timeline.
One row per (agent_id, channel_id); ``updated_at`` is a heartbeat.

Deliberately NOT the ``events`` pipeline — this is a cheap status mirror the
trigger updates around/inside a run; it never affects delivery.

The heartbeat is driven by a **timer**, not by stream events. Before that, the
only writer was ``on_progress`` (fired per observed runtime message), so a long
silent stretch — a slow model's chain-of-thought, a long-running tool — let the
heartbeat go stale and the UI fell back to "queued" while the agent was in fact
working. See ``TurnActivity``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from loguru import logger

# A 'running' row whose heartbeat is older than this reads as "no signal": the
# trigger process died mid-run, or something upstream wedged. Readers surface it
# as its own state rather than silently downgrading it to "queued" — a stalled
# turn and a turn that never started are different things to the user.
ACTIVITY_STALE_SECONDS = 90

# How often the live-turn timer refreshes ``updated_at``. Must stay comfortably
# below ACTIVITY_STALE_SECONDS so an ordinary scheduling hiccup can't trip the
# staleness window.
HEARTBEAT_INTERVAL_SECONDS = 30

# Cap on the per-turn step timeline persisted in ``steps``. Older entries are
# dropped from the front and counted, so the UI can say how many it isn't
# showing instead of silently truncating.
MAX_STEPS = 24


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_ts(value: Any) -> Optional[datetime]:
    """Parse a stored timestamp into an aware UTC datetime, or None."""
    if value is None:
        return None
    try:
        dt = value if isinstance(value, datetime) else datetime.fromisoformat(str(value))
    except (ValueError, TypeError):
        return None
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt


async def _upsert(db, agent_id: str, channel_id: str, fields: dict) -> None:
    """Update the (agent_id, channel_id) row, inserting it if absent."""
    filt = {"agent_id": agent_id, "channel_id": channel_id}
    updated = await db.update("bus_agent_activity", filt, fields)
    if not updated:
        await db.insert("bus_agent_activity", {**filt, **fields})


# --- Reader side (public via ``activity.py``) -------------------------------


def is_live(row: Any) -> bool:
    """True if the row is a fresh 'running' heartbeat (not stale/dead)."""
    if not row or row.get("state") != "running":
        return False
    dt = _parse_ts(row.get("updated_at"))
    if dt is None:
        return False
    return (datetime.now(timezone.utc) - dt).total_seconds() < ACTIVITY_STALE_SECONDS


def is_stalled(row: Any) -> bool:
    """True if the row claims 'running' but its heartbeat has gone stale.

    Distinct from ``queued``: the turn DID start, we simply stopped hearing
    from it. Collapsing the two is what made a wedged bus indistinguishable
    from a busy one in the team chat.
    """
    return bool(row) and row.get("state") == "running" and not is_live(row)


def parse_steps(row: Any) -> Dict[str, Any]:
    """Normalise the stored ``steps`` blob into ``{items, dropped}``.

    Always returns the full shape so callers never branch on storage details;
    an unparseable / absent blob reads as an empty timeline.
    """
    empty: Dict[str, Any] = {"items": [], "dropped": 0}
    if not row:
        return empty
    raw = row.get("steps")
    if not raw:
        return empty
    try:
        blob = raw if isinstance(raw, dict) else json.loads(raw)
    except (ValueError, TypeError):
        return empty
    if not isinstance(blob, dict):
        return empty
    items = blob.get("items")
    return {
        "items": items if isinstance(items, list) else [],
        "dropped": int(blob.get("dropped") or 0),
    }


async def get_channel_activity(db, channel_id: str) -> List[dict]:
    """All activity rows for a channel (for the team-chat status view)."""
    return await db.get("bus_agent_activity", {"channel_id": channel_id})


# --- Writer side (MessageBusTrigger only) ----------------------------------


class TurnActivity:
    """Owns the activity mirror for ONE team-room turn.

    Combines the three things that used to be scattered across the trigger:
    the start/end row writes, the phase-transition step log, and — new — a
    timer heartbeat that refreshes ``updated_at`` whether or not the runtime
    is emitting anything. Use via ``turn()``; nothing here may raise into the
    run (status must never break delivery).
    """

    def __init__(self, db, agent_id: str, channel_id: str) -> None:
        self._db = db
        self._agent_id = agent_id
        self._channel_id = channel_id
        self._phase: Optional[str] = None
        self._tool_count = 0
        self._steps: List[dict] = []
        self._dropped = 0
        self._beat: Optional[asyncio.Task] = None
        self._event_id: Optional[str] = None

    # -- lifecycle ----------------------------------------------------------

    async def start(self, phase: str = "starting") -> None:
        now = _now_iso()
        self._phase = phase
        self._steps = [{"phase": phase, "at": now}]
        await _upsert(self._db, self._agent_id, self._channel_id, {
            "state": "running", "phase": phase, "tool_count": 0,
            "steps": self._encode_steps(), "event_id": None,
            "started_at": now, "updated_at": now,
        })
        self._beat = asyncio.create_task(self._heartbeat_loop())
        # Paired done-callback: an unawaited task's exception would otherwise
        # only surface as a GC warning (incident lesson #2).
        self._beat.add_done_callback(self._on_beat_done)

    async def finish(self) -> None:
        """Stop the heartbeat and flip the row to idle.

        ``steps`` is deliberately KEPT: together with the idle ``updated_at``
        (= finish time) it lets the UI show what the agent just did, not only
        what it is doing.
        """
        if self._beat is not None:
            self._beat.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._beat
            self._beat = None
        try:
            await _upsert(self._db, self._agent_id, self._channel_id, {
                "state": "idle", "phase": None, "tool_count": self._tool_count,
                "steps": self._encode_steps(), "updated_at": _now_iso(),
            })
        except Exception as e:  # noqa: BLE001 — status write must never break delivery
            logger.warning(f"[bus-activity] finish failed for {self._agent_id}: {e}")

    # -- progress -----------------------------------------------------------

    async def on_progress(self, kind: str, tool_name: Optional[str] = None) -> None:
        """``run_collector`` progress sink: record phase transitions.

        Only a CHANGE of phase writes a row — the liveness signal is the timer,
        so per-delta writes buy nothing. ``tool_count`` accumulates locally and
        rides along on the next phase write or heartbeat.
        """
        if kind == "tool":
            self._tool_count += 1
            phase = f"tool:{tool_name}" if tool_name else "tool"
        elif kind == "thinking":
            phase = "thinking"
        elif kind == "response":
            phase = "replying"
        else:
            return
        if phase == self._phase:
            return
        self._phase = phase
        self._append_step(phase)
        try:
            await _upsert(self._db, self._agent_id, self._channel_id, {
                "state": "running", "phase": phase, "tool_count": self._tool_count,
                "steps": self._encode_steps(), "updated_at": _now_iso(),
            })
        except Exception as e:  # noqa: BLE001 — status write must never break delivery
            logger.warning(f"[bus-activity] phase write failed for {self._agent_id}: {e}")

    async def note_event_id(self, event_id: str) -> None:
        """Bind this turn's events-row id to the activity row.

        Called once by ``collect_run`` when the Step-0 progress message
        surfaces the id. Lets the team UI fetch the finished turn's full
        event_log via the existing event-log endpoint. Never raises into
        the run.
        """
        if not event_id or self._event_id:
            return
        self._event_id = event_id
        try:
            await _upsert(self._db, self._agent_id, self._channel_id, {
                "event_id": event_id, "updated_at": _now_iso(),
            })
        except Exception as e:  # noqa: BLE001 — status write must never break delivery
            logger.warning(f"[bus-activity] event_id write failed for {self._agent_id}: {e}")

    # -- internals ----------------------------------------------------------

    def _append_step(self, phase: str) -> None:
        self._steps.append({"phase": phase, "at": _now_iso()})
        if len(self._steps) > MAX_STEPS:
            self._dropped += len(self._steps) - MAX_STEPS
            self._steps = self._steps[-MAX_STEPS:]

    def _encode_steps(self) -> str:
        return json.dumps({"items": self._steps, "dropped": self._dropped})

    async def _heartbeat_loop(self) -> None:
        """Refresh ``updated_at`` on a timer for as long as the turn runs.

        Unconditional: it does not care whether the runtime emitted anything.
        That is the whole point — a model that thinks silently for ten minutes
        is a healthy long run (binding rule #14), and the UI must keep saying so.
        """
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            try:
                await _upsert(self._db, self._agent_id, self._channel_id, {
                    "state": "running", "phase": self._phase,
                    "tool_count": self._tool_count, "updated_at": _now_iso(),
                })
            except Exception as e:  # noqa: BLE001 — a missed beat must not end the run
                logger.warning(
                    f"[bus-activity] heartbeat failed for {self._agent_id} "
                    f"in {self._channel_id}: {e}"
                )

    def _on_beat_done(self, task: asyncio.Task) -> None:
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.warning(
                f"[bus-activity] heartbeat task for {self._agent_id} "
                f"in {self._channel_id} died: {exc!r}"
            )


@contextlib.asynccontextmanager
async def turn(db, agent_id: str, channel_id: str):
    """Scope one team-room turn's activity mirror.

    Yields the :class:`TurnActivity` so the caller can hand ``on_progress`` to
    the runtime. The row is flipped to idle and the heartbeat torn down on the
    way out, however the body exits.
    """
    act = TurnActivity(db, agent_id, channel_id)
    await act.start()
    try:
        yield act
    finally:
        await act.finish()
