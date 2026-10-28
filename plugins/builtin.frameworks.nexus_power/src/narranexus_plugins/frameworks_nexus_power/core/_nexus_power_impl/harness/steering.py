"""
@file_name: steering.py
@author: Bin Liang
@date: 2026-07-29
@description: Step-boundary steering inlets. The DRAIN_STEERING call
site exists from day one; the loop never changes, only which inlet is
mounted. Two implementations: NullSteeringInlet (the default, locked
empty by a contract test) and QueueSteeringInlet (the P4 TriggerInbox
seam — an in-process queue the transport layer feeds). Injection is
append-only by contract (a prefix mutation would void the prompt cache).
"""

from __future__ import annotations

import asyncio
from typing import Optional

from narranexus_plugins.frameworks_nexus_power.core.contracts.model import (
    ProviderMessage,
    STEER_ID_KEY,
)
from narranexus_plugins.frameworks_nexus_power.core.contracts.protocols import (
    CancellationSignal,
)

#: How often ``wait_for_input`` re-checks cancellation while blocking. A long
#: wait must still respond to a stop promptly, so the blocking get() is sliced:
#: this bounds the cancel-latency without busy-spinning. 0.1s is a deliberate
#: simplicity choice over racing the get against a cancel-future: CancellationSignal
#: is a poll-only ``requested()`` view (no awaitable to select on), so a periodic
#: re-check is the honest fit. TODO(perf, only if it ever matters): if a signal
#: that exposes an awaitable lands, swap the slice loop for a single
#: ``asyncio.wait({get, cancel_event})`` and drop the poll constant.
_WAIT_CANCEL_POLL_S = 0.1


class NullSteeringInlet:
    """No steering source; always empty."""

    async def drain(self) -> list[ProviderMessage]:
        return []

    def take_consumed(self) -> list[str]:
        return []

    async def wait_for_input(
        self, timeout: float, cancel: Optional[CancellationSignal] = None
    ) -> list[ProviderMessage]:
        # No producer can ever feed this inlet, so a wait would only burn the
        # timeout to no purpose — report the timeout at once (empty). The agent
        # that asked to wait learns "nothing arrived" immediately and closes.
        return []


class QueueSteeringInlet:
    """The P4 TriggerInbox seam: drains an in-process queue each step.

    The loop owns exactly one question — ``drain()`` at the step boundary
    — and knows nothing about where the messages came from. The transport
    layer (the runner's stdin reader locally; the executor's steer
    endpoint in the cloud) is the sole writer to the queue, so a new
    producer (team room, owner chat, …) never reaches into the loop: it
    only puts a ProviderMessage on the queue.

    Drain is snapshot-and-empty, never blocking: the loop calls it on
    every step boundary and the empty inlet is the common case, so it must
    return immediately when nothing is queued rather than await a put().
    Whatever arrived *before* this drain is taken; whatever arrives after
    is seen by the next one. Order is the queue's FIFO — the arrival order
    the producers wrote in.

    Injection stays append-only by the SteeringInlet contract: the drained
    messages are appended after the turn's existing prefix (the loop hands
    them to ``ledger.record_steering``), never rewritten into it, so the
    prompt cache prefix is preserved.

    Writer contract (the transport must honour it; not enforceable here):

    * **Event-loop affinity.** ``asyncio.Queue`` is not thread-safe. The
      writer must ``put_nowait`` on the loop that runs this turn. A writer
      on another thread (e.g. a blocking ``stdin`` reader) must hand the
      put back to that loop via ``loop.call_soon_threadsafe(
      queue.put_nowait, msg)`` (or ``run_coroutine_threadsafe``) — a bare
      cross-thread ``put_nowait`` is a latent lost-wakeup the moment any
      awaiting getter is added.
    * **Back-pressure at the durable write edge, not this queue.** This
      queue is an in-flight hand-off of already-admitted messages, so the
      transport (in-process today: the orchestrator's steer channel) leaves
      it unbounded and ``put_nowait`` never blocks. The bound lives one
      layer up, at the ``steer_inbox`` write edge, where a producer that
      outruns the run is back-pressured (``SteerInboxFull``) — never dropped
      (iron rule #16). ``drain()`` likewise takes the whole backlog and never
      truncates. The invariant that keeps this in-flight queue from growing
      is the ORCHESTRATOR's: it must push into the channel at the loop's
      drain rate (one step-boundary's worth), not empty the whole inbox
      backlog at once — that pacing is the steer-routing PR's to guarantee.
    """

    def __init__(self, queue: asyncio.Queue[ProviderMessage]) -> None:
        self._queue = queue
        # steer_inbox row ids of messages drained (consumed) but not yet
        # reported. The loop reads this after each drain to emit a
        # ``steer_consumed`` signal; the producer marks those rows consumed and
        # only THEN advances its cursor — so a message pushed-but-never-drained
        # is never acked (never lost). A private, transport-only key.
        self._consumed_ids: list[str] = []

    def _take_one(self, msg: ProviderMessage) -> ProviderMessage:
        """Strip the platform bookkeeping key (the model never sees it) and
        record it as consumed, returning the clean message. Shared by ``drain``
        and ``wait_for_input`` so both report consumption. A message without one
        (tests, a non-steer producer) tracks nothing."""
        steer_id = msg.pop(STEER_ID_KEY, None) if isinstance(msg, dict) else None
        if steer_id is not None:
            self._consumed_ids.append(steer_id)
        return msg

    async def drain(self) -> list[ProviderMessage]:
        drained: list[ProviderMessage] = []
        while True:
            try:
                msg = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                return drained
            drained.append(self._take_one(msg))

    def take_consumed(self) -> list[str]:
        """The steer_inbox ids consumed since the last call, then cleared.
        The loop calls this after ``drain`` / ``wait_for_input`` to report
        consumption exactly once."""
        ids, self._consumed_ids = self._consumed_ids, []
        return ids

    async def wait_for_input(
        self, timeout: float, cancel: Optional[CancellationSignal] = None
    ) -> list[ProviderMessage]:
        """Block up to ``timeout`` seconds for the next message, then fuse it
        with whatever else is queued by then. Two outcomes only (the Owner's
        contract): a non-empty list (input arrived) or ``[]`` (timeout / cancelled).

        Fast path: if the queue already has messages, return them without
        waiting. Otherwise a single ``queue.get()`` task is awaited in slices so
        cancellation is honoured within ``_WAIT_CANCEL_POLL_S`` rather than only
        at the deadline. Reusing ONE get task across slices (instead of
        ``wait_for(queue.get(), slice)`` per slice) closes the classic hazard
        where a timed-out ``get`` drops an item it had already dequeued: the
        pending get is cancelled only if it never completed, so a message is
        either returned or left on the queue — never consumed and lost
        (iron rule #16). Consumed ids are tracked (via ``_take_one``) exactly
        like ``drain``, so a message steered into a WAITING run advances the
        producer's cursor too."""
        drained = await self.drain()
        if drained:
            return drained
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        get_task: "asyncio.Task[ProviderMessage]" = asyncio.ensure_future(
            self._queue.get()
        )
        try:
            while True:
                if cancel is not None and cancel.requested():
                    return []
                remaining = deadline - loop.time()
                if remaining <= 0:
                    # Deadline: one last non-blocking sweep before giving up. A
                    # message that resolved the pending get in the final slice is
                    # sitting in the queue (get_task has not dequeued it — had it,
                    # the done() branch above would have returned it), so drain()
                    # takes it NOW instead of leaving the agent told "nothing
                    # arrived" only to see it on the next step's DRAIN. get_nowait
                    # has no await point, so it cannot race the still-pending
                    # get_task; the finally then cancels that get, and with the
                    # queue already emptied that drops nothing. Empty in the common
                    # timeout case → [] → the loop injects the wrap-up notice.
                    return await self.drain()
                await asyncio.wait(
                    {get_task}, timeout=min(_WAIT_CANCEL_POLL_S, remaining)
                )
                if get_task.done():
                    # Strip + track the first item too (it bypassed drain).
                    first = self._take_one(get_task.result())
                    return [first, *await self.drain()]
        finally:
            # Only a get that never dequeued is still pending here (the done
            # branch returns inline), so cancelling it cannot drop a message.
            if not get_task.done():
                get_task.cancel()
