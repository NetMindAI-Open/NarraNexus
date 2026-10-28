"""
@file_name: _thinking_batcher.py
@author: Bin Liang
@date: 2026-05-13
@description: Per-run thinking-delta batcher for WS-tier coalescing.

Background
----------
Claude Code CLI emits ``thinking_item`` events at whatever granularity
the underlying LLM produces tokens. Anthropic-protocol-compatible
providers like NetMind front DeepSeek-V4-Pro (one ZH char per token)
issue tens of thousands of single-character chunks per long run. Each
chunk would otherwise produce one WebSocket frame to the browser,
flooding the front-end main thread with setState calls and DOM mutations.

Iron rule #16 forbids any solution that loses content or reorders the
stream. The fix has to compress the FRAME count without touching the
character payload.

Two tiers, one batcher
----------------------
* **WS tier (this module)** — coalesce small chunks into ~100 ms /
  ≥500-char emissions so the user perceives a smooth typewriter stream
  while WebSocket frames drop ~50-100×. This is the only tier Phase B
  delivers; the DB-tier (write one row per complete thinking segment)
  is added in Phase C when the ``event_stream`` table lands.

Tier purity — a batch is monologue OR provider CoT, never both
--------------------------------------------------------------
Both tiers arrive on this one channel. Coalescing across a switch
produced a batch that was CoT plus the first characters of the
narration, which the display rule (subset == whole) then called CoT —
tearing one sentence in half at whatever byte the window landed on.
Reproduced 2026-08-30 on DeepSeek-V4-Pro through nexus_power::

    content = "...Silence is correct here." + "There"   monologue = "There"
    content = "'s no new user message in this turn..."  (pure)

rendered as "…here.There" receded, then "'s no new user message…"
promoted. So a tier switch is now a flush trigger, ahead of size and
time: every batch carries one tier, and a boundary can only fall where
the tier actually changed.

**What this costs (iron rule #16 accounting).** Nothing on a stream that
does not switch: same triggers, same boundaries, byte-identical output —
pinned by ``test_pure_stream_coalescing_is_not_regressed``. The only new
frames are at real transitions, i.e. bounded by the number of times the
model crosses between reasoning and narration in a turn (roughly twice
per tool call). Against the ~50-100× frame reduction this class exists
for, that is noise; and it buys back the one thing the reduction was
never allowed to cost — the user seeing a sentence whole.

Design choice — push from append, not a timer
---------------------------------------------
We deliberately drive flushes from inside ``append_thinking`` (i.e.
"check if it's been 100 ms since last flush whenever a new chunk
arrives") rather than running a separate asyncio timer task. Reasons:

1. **No coordination overhead.** A timer task would have to share the
   batcher state with the producer; with the push model the producer
   is the sole writer and there is nothing to lock.
2. **Self-quiescent.** When the LLM stops emitting thinking, nothing
   keeps firing flushes for empty buffers.
3. **Predictable test-ability.** The exact moment a flush happens is
   a function of the input stream — no monotonic-clock races.

The cost is that residual content remaining < 100 ms after the LAST
chunk is left in the buffer until the next append OR an explicit
``flush_ws()`` call. The caller is expected to invoke ``flush_ws()``
at stream-end and at type-switch points (tool_call / tool_output
arriving), which closes that gap. This is documented contract — see
``response_processor`` for the call sites.
"""
from __future__ import annotations

import time
from typing import Optional


class _ThinkingBatcher:
    """Coalesces consecutive raw thinking chunks into ~100 ms WebSocket
    frames. Content is preserved verbatim; only frame boundaries shift.

    Lifecycle: one instance per ``ResponseProcessor``. Since every agent
    turn instantiates its own ``ResponseProcessor`` (see
    ``ResponseProcessor.__init__``), the batcher is effectively per-run
    — no cross-turn state, no global registry, no cleanup hook.

    Trigger conditions (any fires a flush):
      * accumulated buffer ≥ ``FLUSH_CHARS``
      * elapsed time since last flush ≥ ``FLUSH_MS``
      * explicit ``flush_ws()`` call from the caller (used at type
        switches and stream end)

    Empty inputs and a fresh batcher both return ``None`` from
    ``append_thinking`` / ``flush_ws`` so callers can use
    ``if (chunk := batcher.append_thinking(content)) is not None:`` as
    a guard without further checks.
    """

    FLUSH_MS = 100
    FLUSH_CHARS = 500

    __slots__ = ("_buf", "_chars", "_last_flush_ts", "_tier", "_flushed_tier")

    def __init__(self) -> None:
        self._buf: list[str] = []
        self._chars: int = 0
        # 0.0 sentinel meaning "no flush has happened yet" — the very
        # first append seeds last_flush_ts so the 100 ms window is
        # measured from real chunk arrival, not from object creation.
        self._last_flush_ts: float = 0.0
        # Tier of what is currently buffered, and of the batch the last
        # flush returned. See the "Tier purity" section in the module
        # docstring for why the buffer may not mix them.
        self._tier: bool = False
        self._flushed_tier: bool = False

    @property
    def flushed_tier(self) -> bool:
        """Was the most recently flushed batch monologue (vs provider CoT)?

        Read straight after a flush returns a payload; it describes THAT
        payload, which is tier-pure by construction.
        """
        return self._flushed_tier

    def append_thinking(self, content: str, is_monologue: bool = False) -> Optional[str]:
        """Buffer a thinking chunk. Returns the coalesced WS-tier
        payload if a flush trigger fired, otherwise ``None``.

        ``is_monologue`` is the chunk's tier. A chunk of the other tier
        closes the current batch FIRST (before its own size/time triggers
        are even considered), so a batch never mixes the two and a tier
        boundary can never land inside a chunk.

        Empty ``content`` is a no-op and returns ``None``.
        """
        if not content:
            return None

        switched: Optional[str] = None
        if self._buf and is_monologue != self._tier:
            # Close the open batch at the switch. Deliberately ahead of the
            # size/time tests: the batch that produced the observed
            # mid-word split was a SHORT, FAST one (a CoT tail plus the
            # first word of the narration), so neither trigger would have
            # fired in time to save it.
            switched = self._flush(time.monotonic())

        self._tier = is_monologue
        self._buf.append(content)
        self._chars += len(content)

        now = time.monotonic()
        if self._last_flush_ts == 0.0:
            # Seed on first append. Subsequent appends use this baseline
            # for the 100 ms test.
            self._last_flush_ts = now

        if switched is not None:
            # One payload per call. The chunk just buffered rides the next
            # trigger or the caller's flush_ws() — the same residual
            # contract this class has always had.
            return switched
        if self._chars >= self.FLUSH_CHARS:
            return self._flush(now)
        if (now - self._last_flush_ts) * 1000 >= self.FLUSH_MS:
            return self._flush(now)
        return None

    def flush_ws(self) -> Optional[str]:
        """Force-emit whatever is in the buffer, regardless of triggers.

        Returns the coalesced payload, or ``None`` if the buffer was
        empty. Callers should invoke this:

        * Whenever a non-thinking event arrives (tool_call_item,
          tool_call_output_item, etc.) so the user sees the residual
          thinking BEFORE the tool call lands in the UI, preserving
          natural chronological order.
        * When the agent loop exits (normal end, cancellation, error)
          so no buffered chunks are silently dropped on shutdown.
        """
        if not self._buf:
            return None
        return self._flush(time.monotonic())

    def has_pending(self) -> bool:
        """True if the buffer has un-flushed content. Useful for
        callers that want to skip ``flush_ws()`` on the hot path when
        nothing is buffered."""
        return bool(self._buf)

    def _flush(self, now: float) -> str:
        content = "".join(self._buf)
        self._buf.clear()
        self._chars = 0
        self._last_flush_ts = now
        self._flushed_tier = self._tier
        return content


__all__ = ["_ThinkingBatcher"]
