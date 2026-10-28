"""
@file_name: wake_signal.py
@author:
@date: 2026-08-17
@description: The cross-process "there is new work" nudge for the poll loop.

`MessageBusTrigger._wake` (2026-08-14) closed the between-turns gap — A posts, B
is mentioned, B waits out the adaptive interval (3-12s) before anyone notices —
but only for posts made by the TRIGGER's own process, because it is an
`asyncio.Event`. Its docstring said so, and named the remedy: "Making it
cross-process means a DB signal and a reader for it".

That remedy stopped being optional when a team reply became a TOOL CALL. The tool
runs on the MCP server, so the room's own relay moved onto exactly the path the
in-process Event never covered; without this module, switching team delivery to a
tool would have handed back part of the latency win measured in `c7739ad1` — and
handed it back as something a person in the room perceives as dead air, which is
what iron rule #16 forbids.

Design notes, in the order they matter:

* **One row, not a queue.** The signal says "look now", never "look at X". The
  poll loop already knows how to find pending work; duplicating that knowledge
  here would give us two answers to one question.
* **Bumped at the write seam.** `LocalMessageBus.send_message` is the only
  `bus_messages` insert in the repository, so putting the bump there makes
  "posted without waking" impossible rather than merely discouraged. That
  retires the structural guard `test_bus_relay_wake` needed while the wake was a
  caller's responsibility.
* **Both dialects, one definition.** Registered in `schema_registry` like every
  other table (iron rule: SQLite and MySQL share the definition), so the desktop
  DMG and the cloud behave identically (iron rule #7).
* **Reads fail OPEN.** An unreadable signal means "no news" and the loop falls
  back to its timer. Raising would take the poll loop down over a latency
  optimisation. The degradation is silent in behaviour but visible in metrics:
  `queue_wait` in the trigger's `[bus-timing]` line is the tier-3 observable
  that catches "the signal stopped working" (iron-rule lesson #4: L1 liveness
  alone is a back door for zombies).
"""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from narranexus.platform.utils.timezone import utc_now

#: Single-row table; the row id is fixed so the write is an upsert of one row
#: rather than an ever-growing log.
TABLE = "bus_wake"
ROW_ID = 1


async def bump(db: Any) -> None:
    """Say "there is new work" — best-effort, never raises.

    Called AFTER a successful insert, deliberately: the signal means work
    exists, so a send that failed must leave it untouched or the loop wakes to
    find nothing and the signal stops meaning anything.
    """
    try:
        stamp = utc_now()
        updated = await db.update(TABLE, {"id": ROW_ID}, {"bumped_at": stamp})
        if not updated:
            # First bump on a fresh database. Insert rather than pre-seeding in
            # a migration: a migration that seeds rows is a migration that can
            # be re-run wrong, and this row's absence is indistinguishable from
            # "no news" to every reader.
            await db.insert(TABLE, {"id": ROW_ID, "bumped_at": stamp})
    except Exception as e:  # noqa: BLE001 — a latency hint must never cost a send
        logger.debug(f"[wake] could not bump the signal: {e}")


async def read(db: Any) -> Optional[str]:
    """The current signal value, as a comparable string. Never raises.

    Returns a sentinel rather than None-on-error so a caller cannot confuse
    "unreadable" with "changed": both read as "no news", which is the fail-open
    behaviour the loop wants.
    """
    try:
        row = await db.get_one(TABLE, {"id": ROW_ID})
        if not row:
            return ""
        return str(row.get("bumped_at") or "")
    except Exception as e:  # noqa: BLE001 — fail open to the timer
        logger.debug(f"[wake] could not read the signal: {e}")
        return ""
