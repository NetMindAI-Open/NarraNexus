"""
@file_name: channel_dedup_store.py
@date: 2026-05-08
@description: Three-layer dedup cascade for IM channel triggers.

Direct extraction of the cascade currently inline in
``LarkTrigger._check_and_classify_event``. The Lark version stays put for
Phase 1 — Phase 2 will switch the trigger over to this generic store.

Layers, cheapest first:

    1. Historic-replay filter (O(1), no I/O)
       ``baseline_ms`` is set whenever the transport reconnects so events
       older than that minus a buffer are treated as platform replays of
       pre-session messages and dropped outright. Without this, a long
       WebSocket disconnect followed by reconnect releases backlogged
       events that pass Layer 2 (memory cache TTL = 10 min) and let the
       agent reply to hour-old messages.

    2. In-memory hot cache (O(1) with lock)
       TTL-bounded hash. ``threading.Lock`` (not ``asyncio.Lock``) because
       SDK callbacks reach this layer from non-async threads (Lark today;
       pattern preserved for any future channel using a thread-based SDK).

    3. Durable DB gate
       ``ChannelSeenMessageRepository.mark_seen`` — survives process
       restarts. Fail-open on backend I/O error: classify returns
       ``layer="db_fail_open"`` so the audit log records DB-driven
       double-processing for post-incident review.

Public surface:

    classify(message_id, create_time_ms) -> dict
        Returns ``{"accept": bool, "layer": str, ...}``. ``layer`` names
        the rejection reason for audit; ``accept=True`` means caller
        should process the message.

    update_baseline(value_ms)
        Caller is responsible for advancing the baseline at the right
        moments — usually process startup and every successful transport
        reconnect. The store does NOT default to ``time.time()`` at init
        because every channel has its own definition of "session start".
"""
from __future__ import annotations

import threading
import time
from typing import Optional

from loguru import logger

from xyz_agent_context.repository.channel_seen_message_repository import (
    ChannelSeenMessageRepository,
)


class ChannelDedupStore:
    """Three-layer dedup cascade. Channel-agnostic."""

    # In-memory hot cache TTL. Comfortably longer than any observed
    # platform re-delivery burst within a single transport session.
    DEFAULT_TTL_SECONDS = 600

    # Historic-replay filter: events older than `baseline - HISTORY_BUFFER_MS`
    # are dropped. 5 min keeps "user pressed send right before restart"
    # traffic flowing while still cutting off hour-old replays.
    DEFAULT_HISTORY_BUFFER_MS = 5 * 60 * 1000

    def __init__(
        self,
        channel: str,
        repo: Optional[ChannelSeenMessageRepository] = None,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
        history_buffer_ms: int = DEFAULT_HISTORY_BUFFER_MS,
        content_window_seconds: int = 0,
    ):
        if not channel:
            raise ValueError("channel must be a non-empty string")
        self._channel = channel
        self._repo = repo
        self._ttl_seconds = ttl_seconds
        self._history_buffer_ms = history_buffer_ms

        # Layer 4 (opt-in): platforms that re-dispatch a message under a NEW
        # message_id (NarraMessenger re-issues an invocation when its 15-min
        # server deadline expires mid-processing — the X1 double-reply
        # incident) defeat every id-keyed layer above. When > 0, a caller-
        # supplied content fingerprint is remembered for this many seconds
        # and a second id with the same fingerprint is dropped. In-memory on
        # purpose: re-dispatch lands in the same subscriber process within
        # the window, and a durable fingerprint would block a user's
        # legitimately repeated message long after the window.
        self._content_window_seconds = content_window_seconds
        self._fingerprint_cache: dict[str, float] = {}

        # Layer 2 state
        self._memory_cache: dict[str, float] = {}
        self._memory_lock = threading.Lock()

        # Layer 1 state
        self._baseline_ms: int = 0

    @property
    def channel(self) -> str:
        return self._channel

    @property
    def baseline_ms(self) -> int:
        return self._baseline_ms

    def update_baseline(self, value_ms: int) -> None:
        """Advance the historic-replay baseline. Monotonic — never goes backwards."""
        if value_ms > self._baseline_ms:
            self._baseline_ms = value_ms

    async def classify(
        self,
        message_id: str,
        create_time_ms: int,
        agent_id: str = "",
        content_fingerprint: str = "",
    ) -> dict:
        """
        Classify an incoming event. Returns a dict with at least:

            accept: bool
            layer: str   — one of:
                "historic", "no_msg_id", "content_dedup", "memory_dedup",
                "db_new", "db_dedup", "db_fail_open", "no_repo"

        Plus optional diagnostic keys (``age_min`` for historic,
        ``error`` for db_fail_open).

        ``agent_id`` partitions the in-memory hot cache: two agents in
        different workspaces (e.g. two Slack binds in the same process)
        could otherwise collide on a ``client_msg_id`` and silently drop
        one agent's message because the other's already-seen entry is
        cached. Defaults to "" for the no-multi-tenant case to keep
        old callers working — the trigger base always passes it.
        """
        # Layer 1: historic-replay filter. Only applies when we know both
        # the event timestamp and the baseline.
        if create_time_ms and self._baseline_ms > 0:
            cutoff = self._baseline_ms - self._history_buffer_ms
            if create_time_ms < cutoff:
                age_min = (self._baseline_ms - create_time_ms) / 60000.0
                return {"accept": False, "layer": "historic", "age_min": age_min}

        if not message_id:
            # No id → can't dedup. Process defensively.
            return {"accept": True, "layer": "no_msg_id"}

        # Layer 4: content-fingerprint window (opt-in — see __init__). Checked
        # before the id layers commit state so a re-dispatch under a fresh id
        # is caught here; an empty fingerprint (caller opted out for this
        # message) always passes. Same threading.Lock discipline as Layer 2.
        if self._content_window_seconds > 0 and content_fingerprint:
            fp_key = f"{agent_id}:{content_fingerprint}"
            now = time.time()
            with self._memory_lock:
                seen_at = self._fingerprint_cache.get(fp_key)
                if seen_at is not None and now - seen_at < self._content_window_seconds:
                    # SLIDING window: a hit refreshes the stamp. With a fixed
                    # window shorter than the worker timeout (30 min), the
                    # platform's SECOND re-dispatch of a long turn would land
                    # past the original stamp and be accepted — X1 again. As
                    # long as re-dispatch intervals stay below the window, a
                    # turn of any length stays covered (铁律 #14 makes long
                    # turns first-class, so the guard must scale with them).
                    self._fingerprint_cache[fp_key] = now
                    return {"accept": False, "layer": "content_dedup"}
                self._fingerprint_cache[fp_key] = now
                cutoff_ts = now - self._content_window_seconds
                self._fingerprint_cache = {
                    k: v for k, v in self._fingerprint_cache.items() if v > cutoff_ts
                }

        # Layer 2: in-memory hot cache. ``threading.Lock`` is intentional;
        # SDK callbacks may reach us from non-async threads.
        cache_key = f"{agent_id}:{message_id}" if agent_id else message_id
        now = time.time()
        with self._memory_lock:
            if cache_key in self._memory_cache:
                return {"accept": False, "layer": "memory_dedup"}
            self._memory_cache[cache_key] = now
            cutoff_ts = now - self._ttl_seconds
            self._memory_cache = {
                k: v for k, v in self._memory_cache.items() if v > cutoff_ts
            }

        # Layer 3: durable DB. Skipped only when no repo is wired (tests
        # may run without one).
        if self._repo is not None:
            try:
                newly_inserted = await self._repo.mark_seen(message_id)
                return {
                    "accept": bool(newly_inserted),
                    "layer": "db_new" if newly_inserted else "db_dedup",
                }
            except Exception as e:  # noqa: BLE001 — fail-open on I/O
                logger.warning(
                    f"ChannelDedupStore[{self._channel}]: DB layer failed for "
                    f"{message_id}: {type(e).__name__}: {e}; fail-open"
                )
                return {
                    "accept": True,
                    "layer": "db_fail_open",
                    "error": f"{type(e).__name__}: {e}",
                }
        return {"accept": True, "layer": "no_repo"}
