"""
@file_name: host_events.py
@author: Bin Liang
@date: 2026-09-04
@description: Fire host events (contracts.events) from backend routes to whatever plugins listen.

Routes used to import a builtin's function for "something changed, react"
moments (login / quota / provider save → job re-arm). They now emit the host
event through the kernel hook registry; the reacting builtin implements the
hook (``backend.hooks``), and with it disabled nothing listens.
"""
from __future__ import annotations

from typing import Any

from loguru import logger


async def emit_host_event(event: str, /, **payload: Any) -> Any:
    """Call every implementation of host ``event``.

    A LISTENER's failure is logged here and swallowed (the request must not
    break because a plugin's reaction did). An event that does not exist
    (``UnknownEntry``: a typo, a renamed hook) propagates — that is a
    programming error, and swallowing it turned "jobs silently stop
    re-arming after login" into one WARNING line nobody reads.
    """
    from narranexus.contracts import UnknownEntry
    from narranexus.platform.utils.host_hooks import call_host_hook

    try:
        return await call_host_hook(event, **payload)
    except UnknownEntry:
        raise
    except Exception as exc:  # noqa: BLE001 — a broken listener must not break the request
        logger.warning(f"[host-event] {event}: a listener failed: {exc}")
        return None


async def notify_user_runnability_changed(user_id: str) -> None:
    """Login / quota top-up / provider or slot save: the user's agents may be runnable again."""
    await emit_host_event("onDidChangeUserRunnability", user_id=user_id)


__all__ = ["emit_host_event", "notify_user_runnability_changed"]
