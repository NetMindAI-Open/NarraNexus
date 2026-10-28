"""
@file_name: steer_inbox_repository.py
@author: Bin Liang
@date: 2026-08-21
@description: The owner of `steer_inbox` — the store for live-steering
injections destined for a running turn.

A producer appends one injection keyed by an OPAQUE ``run_id`` (the
RunRegistry's handle for a live run — this layer never interprets it);
the transport drains a run's unconsumed rows into its ``SteeringInlet``
at the next step boundary and marks them consumed.

Why a store at all when team messages already live in ``bus_messages``:
the table decouples the running loop from heterogeneous producers (team
is a bus message, an owner-chat interjection is not), and ``consumed_at``
is a per-RUN cursor the bus's per-(agent,channel) cursor cannot serve —
see ``schema/steer_schema.py`` for the full argument.

**Delivery semantics (single drainer).** ``consumed_at`` gives at-most-once
delivery UNDER ONE DRAINER PER RUN, which is the design (one live loop, one
transport). ``pull_unconsumed`` then ``mark_consumed`` are two statements
with no claim between them, so two drainers of the same ``run_id`` would
each inject the batch once (the family's ``ArtifactEventRepository`` makes
the same at-least-once trade). If a future path can run two drainers on one
run (e.g. an executor reaped mid-turn and its replacement resuming the same
run), switch to claim-then-read (a conditional UPDATE whose affected rows
are the claim — see ``job_repository.try_acquire_job``); do not rely on
``consumed_at`` alone there.

This is the family's plain-class shape (not a ``BaseRepository`` subclass:
auto-increment id, scoped range queries — same reasoning as
``ArtifactEventRepository``); reads still return ``SteerInjection`` so
callers get provenance typed.
"""

from __future__ import annotations

from datetime import timedelta
from typing import List

from loguru import logger

from narranexus.platform.schema.steer_schema import SteerInjection
from narranexus.platform.utils.db.database import AsyncDatabaseClient
from narranexus.platform.utils.db.dialect_errors import is_unique_violation
from narranexus.platform.utils.db.schema_registry import varchar_width
from narranexus.platform.utils.timezone import to_datetime6_literal, utc_now

#: One injection's content ceiling, in UTF-8 bytes. A steer injection is a
#: single chat/room message; anything past this is a producer bug or abuse.
MAX_CONTENT_BYTES = 128 * 1024

#: How many UNCONSUMED rows one run may accumulate before the write edge
#: pushes back. The bound lives HERE (the bounded-queue write edge), never in
#: ``pull_unconsumed`` / the loop's ``drain()`` — those take the whole batch
#: and must never drop or truncate (iron rule #16, matching steering.py's
#: contract). Over the limit, ``append`` raises so the producer backs off.
MAX_UNCONSUMED_PER_RUN = 500

#: Caller-controllable VARCHAR columns that must fit their declared width.
#: SQLite (TEXT) accepts any length silently; MySQL strict mode raises 1406 —
#: so an unclamped value passes every local test and fails 100% on dev/prod.
#: All are REJECTED over-width, never truncated: for the id columns, clipping
#: two distinct values to the same one would silently break dedup; ``role`` is
#: a short tag with no reason to be clipped either. (``source`` is a Literal,
#: bounded by the type; ``content`` is MEDIUMTEXT, bounded by MAX_CONTENT_BYTES.)
_WIDTH_CHECKED_COLUMNS = ("run_id", "msg_id", "sender_id", "role")


class SteerInboxFull(Exception):
    """A run's unconsumed backlog is at ``MAX_UNCONSUMED_PER_RUN``. The
    producer must back off (queue upstream / retry) — the write edge never
    drops a message to make room (iron rule #16)."""


class SteerInboxRepository:
    def __init__(self, db: AsyncDatabaseClient):
        self._db = db

    async def append(self, inj: SteerInjection) -> bool:
        """Persist one injection. Returns True if inserted, False if a row
        with the same ``(run_id, msg_id)`` already exists.

        Idempotent so a re-delivered message injects at most once — an atomic
        INSERT-or-detect-UNIQUE (the ``channel_seen_message`` shape): only a
        unique-key violation is swallowed as the duplicate it is; any other
        insert failure (a real bug) re-raises rather than masquerading as a
        duplicate.

        Bounds the write edge before inserting (iron rule #16 — reject, never
        drop/truncate): identity columns must fit their width, content must be
        under ``MAX_CONTENT_BYTES``, and the run's backlog under
        ``MAX_UNCONSUMED_PER_RUN``.

        Raises:
            ValueError: an identity column overflows its width, or content
                exceeds ``MAX_CONTENT_BYTES``.
            SteerInboxFull: the run's unconsumed backlog is at the cap.
            Exception: any non-unique insert failure (caller fails open).
        """
        for col in _WIDTH_CHECKED_COLUMNS:
            value = getattr(inj, col)
            width = varchar_width("steer_inbox", col)
            if len(value) > width:
                raise ValueError(
                    f"steer_inbox.{col} is {len(value)} chars, over its {width} "
                    f"width — reject rather than clip an identity column"
                )
        if len(inj.content.encode("utf-8")) > MAX_CONTENT_BYTES:
            raise ValueError(
                f"steer injection content is {len(inj.content.encode('utf-8'))} "
                f"bytes, over the {MAX_CONTENT_BYTES}-byte write-edge cap"
            )
        if await self._unconsumed_count(inj.run_id) >= MAX_UNCONSUMED_PER_RUN:
            raise SteerInboxFull(
                f"run {inj.run_id} has {MAX_UNCONSUMED_PER_RUN}+ unconsumed "
                f"injections — producer must back off"
            )

        try:
            await self._db.insert(
                "steer_inbox",
                {
                    "run_id": inj.run_id,
                    "msg_id": inj.msg_id,
                    "role": inj.role,
                    "content": inj.content,
                    "sender_id": inj.sender_id,
                    "source": inj.source,
                },
            )
        except Exception as e:  # noqa: BLE001 — unique = duplicate; else re-raise
            if is_unique_violation(e):
                logger.debug(
                    f"[steer-inbox] duplicate append for "
                    f"{inj.run_id}/{inj.msg_id}: {e}"
                )
                return False
            raise
        return True

    async def _unconsumed_count(self, run_id: str) -> int:
        rows = await self._db.execute(
            "SELECT COUNT(*) AS n FROM steer_inbox "
            "WHERE run_id = %s AND consumed_at IS NULL",
            params=(run_id,),
            fetch=True,
        )
        return int(rows[0]["n"]) if rows else 0

    async def pull_unconsumed(self, run_id: str) -> List[SteerInjection]:
        """The run's still-pending injections, oldest first (arrival order).

        Takes the whole backlog, never a ``LIMIT`` — the loop's ``drain()``
        must not drop or truncate (iron rule #16); the cap lives at the write
        edge (``append``), not here."""
        rows = await self._db.execute(
            "SELECT id, run_id, msg_id, role, content, sender_id, source, "
            "created_at, consumed_at FROM steer_inbox "
            "WHERE run_id = %s AND consumed_at IS NULL ORDER BY id",
            params=(run_id,),
            fetch=True,
        )
        return [SteerInjection(**row) for row in (rows or [])]

    async def mark_consumed(self, run_id: str, up_to_id: int) -> int:
        """Stamp ``consumed_at`` on this run's pending rows with id <=
        ``up_to_id``. Returns how many rows it consumed.

        Scoped to ``run_id`` and to ``consumed_at IS NULL`` so it never
        touches another run's backlog and re-consuming is a no-op. The id
        ceiling is what lets a drain consume exactly the window it saw and
        leave anything that arrived after it pending (no silent loss). The
        stamp goes through ``to_datetime6_literal`` so it lands in the same
        byte format as ``created_at``'s ``datetime('now')`` default (a raw-SQL
        param bypasses the dict serializer)."""
        result = await self._db.execute(
            "UPDATE steer_inbox SET consumed_at = %s "
            "WHERE run_id = %s AND id <= %s AND consumed_at IS NULL",
            params=(to_datetime6_literal(utc_now()), run_id, up_to_id),
            fetch=False,
        )
        return result if isinstance(result, int) else 0

    async def mark_consumed_by_msg_ids(
        self, run_id: str, msg_ids: "list[str]"
    ) -> int:
        """Stamp ``consumed_at`` on the EXACT ``(run_id, msg_id)`` rows the loop
        reported draining. Returns how many rows it consumed.

        The consumer (the loop) reports the msg_ids it drained, not a row-id
        ceiling — a drain takes the whole queued window, so marking the exact set
        is both precise and equivalent to a ceiling here, and it needs no row id
        threaded back through the transport (``append`` still returns a bool).
        Scoped to ``consumed_at IS NULL`` so re-consuming is a no-op; the stamp
        goes through ``to_datetime6_literal`` for the same byte format as
        ``created_at`` (raw-SQL param bypasses the dict serializer)."""
        if not msg_ids:
            return 0
        marks = ", ".join(["%s"] * len(msg_ids))
        result = await self._db.execute(
            f"UPDATE steer_inbox SET consumed_at = %s "
            f"WHERE run_id = %s AND msg_id IN ({marks}) AND consumed_at IS NULL",
            params=(to_datetime6_literal(utc_now()), run_id, *msg_ids),
            fetch=False,
        )
        return result if isinstance(result, int) else 0

    async def discard_run(self, run_id: str) -> int:
        """DELETE this run's still-UNCONSUMED rows. Returns how many.

        Called when a run is released — the moment "these rows will never be
        drained" becomes certain (the SteerChannel is gone, the loop has ended).
        Without this, a row that was pushed but the turn ended before draining it
        stays ``consumed_at IS NULL`` forever, so ``cleanup_older_than_days`` (its
        guard is ``consumed_at IS NOT NULL``) can never reclaim it → the table
        grows unbounded. DELETE, not mark-consumed: stamping a never-drained row
        ``consumed`` would lie to any future audit of "which injections actually
        reached a model". Must run AFTER the run's last possible drain — running
        it before release would race an in-flight ``deliver_consumed`` (row gone,
        ``mark_consumed_by_msg_ids`` finds nothing, cursor still advances → a real
        loss)."""
        result = await self._db.execute(
            "DELETE FROM steer_inbox WHERE run_id = %s AND consumed_at IS NULL",
            params=(run_id,),
            fetch=False,
        )
        return result if isinstance(result, int) else 0

    async def cleanup_older_than_days(self, days: int, orphan_days: int) -> int:
        """Delete CONSUMED rows older than ``days`` AND UN-consumed ORPHAN rows
        older than ``orphan_days``. Returns rows deleted (best-effort; 0 on
        driver error).

        The family's retention contract (``channel_seen_message``,
        ``lark_seen_message``, ``channel_trigger_audit``) — run by
        ``MessageBusTrigger._maybe_run_steer_cleanup`` (startup + daily), which
        passes the retention bounds from its ``STEER_RETENTION_DAYS`` /
        ``STEER_ORPHAN_DAYS`` constants (repository stays pure CRUD; the values
        are the caller's policy). Two reclaim arms:

        * consumed rows past ``days`` — normal retention. ``consumed_at IS NOT
          NULL`` so a long-running turn's not-yet-injected messages are never
          deleted (iron rule #16).
        * rows STILL unconsumed past ``orphan_days`` — the STRUCTURAL backstop
          for orphans (a row pushed but never drained). The producer discards
          these at run teardown (``_discard_steer_orphans``), but that has a
          narrow race (an append landing after the teardown DELETE); this arm
          reclaims whatever slips through. ``orphan_days`` is DELIBERATELY
          generous (7d ≫ any turn, including the tens-of-hours jobs iron rule #14
          allows — whose rows drain AS the run runs, so they are never unconsumed
          for days): no live, actively-draining run can own a row that old, so
          this never deletes a legitimately-pending injection.

        Prunes by ``created_at`` (uniform format via the DB default), not
        ``consumed_at`` which a raw param could write in a second SQLite
        format."""
        now = utc_now()
        cutoff = to_datetime6_literal(now - timedelta(days=days))
        orphan_cutoff = to_datetime6_literal(now - timedelta(days=orphan_days))
        try:
            result = await self._db.execute(
                "DELETE FROM steer_inbox WHERE "
                "(created_at < %s AND consumed_at IS NOT NULL) "
                "OR (created_at < %s AND consumed_at IS NULL)",
                params=(cutoff, orphan_cutoff),
                fetch=False,
            )
            return int(result) if isinstance(result, (int, float)) else 0
        except Exception as e:  # noqa: BLE001 — retention is best-effort
            logger.warning(
                f"[steer-inbox] cleanup_older_than_days({days}): "
                f"{type(e).__name__}: {e}"
            )
            return 0
