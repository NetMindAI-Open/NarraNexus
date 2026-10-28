"""
@file_name: control.py
@author:
@date: 2026-09-22
@description: Who is driving the browser right now — the agent, or the user.

The panel is interactive (Owner decision 2026-09-22: watch and take over), so two parties
can reach the same browser. Three rules make that safe rather than chaotic:

**One driver at a time, and the UI can always name them.** Codex does this by
showing a distinct agent cursor; we expose ``holder`` so the panel can say it.

**Handover is explicit, never a grab.** Stray mouse movement over the panel
does not steal the pointer — an agent halfway through filling a form would
otherwise leave a half-submitted one behind.

**A queued agent is a working agent.** While the user holds control the
agent's actions *wait*; they do not fail. This is 铁律 #14 applied to a case
that looks different but is not: turning "a human picked up the mouse" into a
tool error would make the user's own helpfulness look like a malfunction, and
would also put a ceiling on how long a takeover may last. There is
deliberately no timeout here.
"""
from __future__ import annotations

import asyncio
from typing import Callable, Literal

from loguru import logger

#: Who currently drives. The agent starts as holder: it opened the session,
#: and making it ask for the pointer before its first action would be a
#: prompt with no information in it.
Holder = Literal["agent", "user"]


class ControlArbiter:
    """Arbitrates the pointer between the agent and the user."""

    def __init__(self) -> None:
        self._holder: Holder = "agent"
        self._waiters: list[asyncio.Future] = []
        self._closed = False
        self._owner: str | None = None
        self._listeners: list[Callable[[dict], None]] = []

    # ── state ────────────────────────────────────────────────────────────

    @property
    def holder(self) -> Holder:
        return self._holder

    def agent_may_act(self) -> bool:
        """True when the agent can act right now without waiting."""
        return self._holder == "agent" and not self._closed

    def accept_user_input(self, connection_id: str = "local") -> bool:
        """Whether an input event from the panel should be forwarded.

        False unless the user explicitly took control. The panel still streams
        frames while the agent drives — watching is always allowed; it is
        *driving* that is exclusive.
        """
        return self._owner == connection_id and self._holder == "user" and not self._closed

    def to_dict(self) -> dict:
        """For the panel header: who drives, and is the agent blocked on us."""
        return {
            "holder": self._holder,
            "agent_waiting": bool(self._waiters),
            "owner_connection_id": self._owner,
        }

    def subscribe(self, listener: Callable[[dict], None]) -> Callable[[], None]:
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _broadcast(self) -> None:
        for listener in list(self._listeners):
            try:
                listener(self.to_dict())
            except Exception:
                logger.exception("browser control listener failed")

    # ── handover ─────────────────────────────────────────────────────────

    def user_take_control(self, connection_id: str = "local") -> bool:
        """Explicit takeover. Idempotent."""
        if self._closed or self._owner not in (None, connection_id):
            return False
        self._holder = "user"
        self._owner = connection_id
        self._broadcast()
        return True

    def user_release_control(self, connection_id: str = "local") -> bool:
        """Hand the pointer back and wake everything that was queued."""
        if self._owner != connection_id:
            return False
        self._holder = "agent"
        self._owner = None
        waiters, self._waiters = self._waiters, []
        for fut in waiters:
            if not fut.done():
                fut.set_result(None)
        self._broadcast()
        return True

    def close(self) -> None:
        """End the session, failing anything still queued.

        Waiters must be failed rather than abandoned: an agent turn left
        awaiting a promise nobody will resolve is a hang with no diagnosis
        (incident lesson #2).
        """
        self._closed = True
        self._owner = None
        self._holder = "agent"
        waiters, self._waiters = self._waiters, []
        for fut in waiters:
            if not fut.done():
                fut.set_exception(ConnectionError("browser session closed"))
        self._broadcast()

    # ── queueing ─────────────────────────────────────────────────────────

    async def wait_for_turn(self) -> None:
        """Block until the agent may act. No deadline, by design (铁律 #14).

        Raises:
            ConnectionError: The session closed while queued.
        """
        while not self.agent_may_act():
            if self._closed:
                raise ConnectionError("browser session closed")
            fut: asyncio.Future = asyncio.get_running_loop().create_future()
            self._waiters.append(fut)
            self._broadcast()
            try:
                await fut
            finally:
                if fut in self._waiters:
                    self._waiters.remove(fut)
                self._broadcast()
