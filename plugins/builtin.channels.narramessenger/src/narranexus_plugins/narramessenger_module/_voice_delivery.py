"""
@file_name: _voice_delivery.py
@date: 2026-08-06
@description: F28 voice delivery — live m.text lifecycle + TTS sanitizer.

Contract source: Hybrid "Direct Matrix RTC fast reply" handoff section 6.
The bridge owns one voice turn's outbound lifecycle: the first playable
text goes out immediately as a base ``m.text`` carrying the
``org.matrix.msc4357.live`` marker (never wait for the full answer);
subsequent text rides ``m.replace`` edits with the CUMULATIVE sanitized
text; the final edit removes the live marker from both content levels.
Hybrid's LiveKit worker observes these events and feeds TTS.

Pure logic by injection: the Matrix ``send`` callable and the clock are
constructor arguments, so the full lifecycle is unit-testable without a
homeserver. Sender failures never raise into the event loop that feeds
the bridge — the bridge goes "broken", keeps accumulating text, and
``close()`` reports ``finalized_ok=False`` so the trigger falls back to
the plain delivery path (handoff: never leave a permanent live state
unresolved without a fallback delivery).
"""
from __future__ import annotations

import re
import time
from typing import Awaitable, Callable, Optional, Tuple

from loguru import logger

LIVE_MARKER_KEY = "org.matrix.msc4357.live"

_CODE_FENCE_RE = re.compile(r"```.*?(```|\Z)", re.DOTALL)
_INLINE_CODE_RE = re.compile(r"`([^`]*)`")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_URL_RE = re.compile(r"https?://\S+")
_HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s*", re.MULTILINE)
_BULLET_RE = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+", re.MULTILINE)
# Emoji / pictographs / symbol blocks that TTS should never receive.
_EMOJI_RE = re.compile(
    "["
    "\U0001f000-\U0001faff"
    "\U00002600-\U000027bf"
    "\U0001f1e6-\U0001f1ff"
    "⬀-⯿"
    "️"
    "]"
)
_SENTENCE_END_RE = re.compile(r"[.!?。！？…][\"')\]]?\s*$")


def sanitize_for_tts(text: str) -> str:
    """Reduce model text to a TTS-safe spoken string.

    Structure-layer hard guarantee (PRD 4.3): even if the model slips,
    markdown / code / URLs / emoji never reach the voice pipeline. Code
    blocks are DROPPED (unreadable aloud); URLs are removed (the spoken
    register says the link goes to the chat); markdown markers are
    stripped keeping their inner text; whitespace collapses to single
    spaces.
    """
    t = _CODE_FENCE_RE.sub(" ", text)
    t = _INLINE_CODE_RE.sub(r"\1", t)
    t = _MD_LINK_RE.sub(r"\1", t)
    t = _URL_RE.sub("", t)
    t = _HEADING_RE.sub("", t)
    t = _BULLET_RE.sub("", t)
    t = t.replace("**", "").replace("__", "")
    t = re.sub(r"(?<!\w)[*_]+|[*_]+(?!\w)", "", t)
    t = _EMOJI_RE.sub("", t)
    return re.sub(r"\s+", " ", t).strip()


class VoiceDeliveryBridge:
    """Owns the Matrix live-reply lifecycle for ONE voice turn.

    Feed it ``on_reply_delta`` (streamed speak arguments) and optionally
    ``on_segment_text`` (the authoritative full text when a speak call
    completes); call ``close()`` exactly once when the run stream ends.
    """

    def __init__(
        self,
        *,
        send: Callable[[dict], Awaitable[str]],
        clock: Callable[[], float] = time.monotonic,
        flush_interval_s: float = 0.4,
    ) -> None:
        self._send = send
        self._clock = clock
        self._interval = flush_interval_s
        self._segments: list[str] = []  # sanitized text of completed speak calls
        self._current_raw = ""  # raw accumulating text of the open speak call
        self._current_call: Optional[str] = None
        self._base_event_id: Optional[str] = None
        # Cadence starts at construction: a mid-word fragment never goes
        # out instantly; the first flush is the first sentence boundary or
        # the first interval tick — whichever comes first.
        self._last_flush = clock()
        self._last_sent_text = ""
        self._broken = False
        self._closed = False
        # Observability stamps (handoff section 9 mapping): first speak
        # delta ≈ first_model_token; first successful send =
        # first_matrix_live_reply_sent; close = matrix_live_reply_finalized.
        self.first_delta_at: Optional[float] = None
        self.first_sent_at: Optional[float] = None
        self.finalized_at: Optional[float] = None

    # ── event intake ────────────────────────────────────────────────────

    async def on_reply_delta(self, *, call_id: str, delta: str) -> None:
        """Streamed increment of a speak call's text argument."""
        if self._closed:
            return
        if self.first_delta_at is None:
            self.first_delta_at = self._clock()
        if call_id != self._current_call:
            self._close_segment()
            self._current_call = call_id
        self._current_raw += delta
        await self._maybe_flush()

    def on_segment_text(self, *, call_id: str, text: str) -> None:
        """Authoritative full text of a completed speak call.

        Corrects the delta view (or substitutes for it entirely when arg
        deltas were unavailable). Same-segment vs new-segment:

        - Explicit ids, when BOTH sides have one, are authoritative in
          both directions: same id -> correct in place (even if the text
          is disjoint), different id -> new segment. NOTE: today this
          branch is defensive only — run_collector does not propagate
          tool_call_id into PROGRESS, so the real event always carries an
          empty string (dev probe 2026-08-07) and the prefix judge below
          is the SOLE guard for the no-delta multi-call path.
        - Otherwise: forward prefix on the RAW text. The authoritative
          text of the SAME call is a superset of (or equal to) its
          accumulated deltas BY CONSTRUCTION on raw — never compare after
          sanitize_for_tts, which deletes paired structure markers and
          breaks the prefix relation exactly when a delta is cut inside a
          markdown link / inline code / URL (the inputs sanitize exists
          for). No reverse branch: a shorter disjoint text is a NEW
          segment — replacing a longer finished segment with a shorter
          one would silently lose spoken content, which is worse than
          duplicating it.
        """
        if self._closed:
            return
        if call_id and self._current_call:
            same_segment = call_id == self._current_call
        else:
            same_segment = not self._current_raw or text.startswith(
                self._current_raw
            )
        if not same_segment:
            self._close_segment()
        self._current_call = call_id or self._current_call
        self._current_raw = text

    # ── lifecycle ───────────────────────────────────────────────────────

    async def close(self) -> Tuple[Optional[str], bool]:
        """Finalize the live reply. Returns ``(spoken_text, finalized_ok)``.

        ``(None, True)`` when nothing was spoken (no events were sent, so
        there is nothing to finalize). ``finalized_ok=False`` means the
        live lifecycle could not be completed — the caller must deliver
        ``spoken_text`` via the plain fallback path so the answer still
        reaches the room (handoff 6.3).
        """
        if self._closed:
            return (self._last_sent_text or None), not self._broken
        self._closed = True
        self.finalized_at = self._clock()
        self._close_segment()
        text = self._cumulative()
        if not text:
            return None, True
        # Attempt the final (marker-free) emit even when a mid-stream send
        # broke: a successful final edit both delivers the full text AND
        # closes the live state on the base event — otherwise the observer
        # is left with a permanently-live stream plus a duplicate fallback
        # message. Only when this attempt also fails do we report not-
        # finalized and let the trigger's plain sender take over.
        try:
            await self._emit(text, live=False)
        except Exception as e:  # noqa: BLE001 — fallback path takes over
            logger.warning(f"[voice-bridge] final edit failed: {e}")
            return text, False
        return text, True

    # ── internals ───────────────────────────────────────────────────────

    def _close_segment(self) -> None:
        segment = sanitize_for_tts(self._current_raw)
        # An exactly-identical consecutive segment carries zero new
        # content (2026-08-13: the model called speak twice with the same
        # text and the room heard it twice) — dropping it cannot lose
        # anything. Equality only: anything shorter/longer/disjoint keeps
        # the "content loss is worse than duplication" rule intact.
        if segment and not (self._segments and self._segments[-1] == segment):
            self._segments.append(segment)
        self._current_raw = ""

    def _cumulative(self) -> str:
        current = sanitize_for_tts(self._current_raw)
        # While an open call is re-speaking the previous segment (its
        # sanitized text is a prefix of it), keep it out of the live view
        # — flushes would stream the duplicate to TTS as it grows. If it
        # ever diverges it enters whole (rare deliberate repetition stays
        # deliverable).
        if current and self._segments and self._segments[-1].startswith(current):
            current = ""
        parts = [*self._segments, current]
        return " ".join(p for p in parts if p).strip()

    async def _maybe_flush(self) -> None:
        if self._broken:
            return
        text = self._cumulative()
        if not text or text == self._last_sent_text:
            return
        at_boundary = bool(_SENTENCE_END_RE.search(self._current_raw))
        interval_due = (self._clock() - self._last_flush) >= self._interval
        if not (at_boundary or interval_due):
            return
        try:
            await self._emit(text, live=True)
        except Exception as e:  # noqa: BLE001 — never kill the event loop
            logger.warning(f"[voice-bridge] live send failed: {e}")
            self._broken = True

    async def _emit(self, text: str, *, live: bool) -> None:
        if self._base_event_id is None:
            content: dict = {"msgtype": "m.text", "body": text}
            if live:
                content[LIVE_MARKER_KEY] = {}
            self._base_event_id = await self._send(content)
        else:
            new_content: dict = {"msgtype": "m.text", "body": text}
            content = {
                "msgtype": "m.text",
                # "* " prefix: the MSC2676 fallback body for pre-edit clients.
                "body": f"* {text}",
                "m.new_content": new_content,
                "m.relates_to": {
                    "rel_type": "m.replace",
                    "event_id": self._base_event_id,
                },
            }
            if live:
                content[LIVE_MARKER_KEY] = {}
                new_content[LIVE_MARKER_KEY] = {}
            await self._send(content)
        self._last_sent_text = text
        self._last_flush = self._clock()
        if self.first_sent_at is None:
            self.first_sent_at = self._last_flush
