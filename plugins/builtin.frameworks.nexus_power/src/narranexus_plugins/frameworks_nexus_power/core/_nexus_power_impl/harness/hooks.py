"""
@file_name: hooks.py
@author: Bin Liang
@date: 2026-07-29
@description: HookRegistry — the full lifecycle event enum defined on
day one, fire sites planted on day one.

A fire with no listeners is a free no-op, so the loop and dispatcher
carry every call site from the start; attaching behaviour later never
touches the call sites. Failure posture is a REGISTRATION property:
safety listeners register ``failure="closed"`` (listener error ⇒ veto),
observability listeners register ``failure="open"`` (log and proceed).
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import Enum, auto
from typing import Any, Awaitable, Callable, Literal

from loguru import logger


class HookEvent(Enum):
    """Lifecycle surface (Codex-verified event roster)."""

    PRE_TOOL_USE = auto()
    POST_TOOL_USE = auto()
    SESSION_START = auto()
    SESSION_END = auto()
    PRE_COMPACT = auto()
    POST_COMPACT = auto()
    SUBAGENT_START = auto()
    SUBAGENT_STOP = auto()
    USER_PROMPT_SUBMIT = auto()
    PERMISSION_REQUEST = auto()
    STOP = auto()


HookFn = Callable[[dict[str, Any]], Awaitable[Any]]


@dataclass(frozen=True)
class HookOutcome:
    """Aggregated result of one fire."""

    allowed: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)


class HookRegistry:
    """Listener registration + firing."""

    def __init__(self) -> None:
        self._listeners: dict[HookEvent, list[tuple[HookFn, str]]] = defaultdict(list)

    @classmethod
    def empty(cls) -> "HookRegistry":
        return cls()

    def on(
        self,
        event: HookEvent,
        fn: HookFn,
        *,
        failure: Literal["open", "closed"],
    ) -> None:
        self._listeners[event].append((fn, failure))

    async def fire(self, event: HookEvent, payload: dict[str, Any]) -> HookOutcome:
        listeners = self._listeners.get(event)
        if not listeners:
            return HookOutcome()
        allowed = True
        notes: list[str] = []
        for fn, failure in listeners:
            try:
                result = await fn(payload)
                if result is False:
                    allowed = False
                    notes.append(f"{event.name}: listener vetoed")
            except Exception as exc:  # noqa: BLE001 - posture decides
                if failure == "closed":
                    allowed = False
                    notes.append(f"{event.name}: closed listener failed: {exc}")
                else:
                    logger.warning(f"open hook listener failed on {event.name}: {exc}")
        return HookOutcome(allowed=allowed, notes=tuple(notes))
