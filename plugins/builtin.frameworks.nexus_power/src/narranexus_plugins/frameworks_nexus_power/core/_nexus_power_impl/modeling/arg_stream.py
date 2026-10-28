"""
@file_name: arg_stream.py
@author: Bin Liang
@date: 2026-07-29
@description: Streaming tool-argument extraction — the technical core of
"the agent's reply streams too".

Expressive/label tools declare ``streamable_fields``; while the model
generates the call's argument JSON character by character, this
extractor surfaces those fields' text incrementally on the ui track, so
the user reads the reply as it is being written. The model track still
records only the complete call — logs, replay and cache semantics are
untouched.

Implemented as a real streaming JSON tokenizer (not regex, not
re-parsing): a container stack for structure, key/value position
tracking per object, and escape / ``\\uXXXX`` handling across arbitrary
fragment boundaries. Disciplines learned from pi's ``pi-ai`` (fragments
split anywhere; block events are not contiguous; consume defensively)
are honoured by construction. Only root-level string fields stream —
nested occurrences of a declared name never leak.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: What an unpaired UTF-16 surrogate decodes to — mirrors
#: ``scrub_surrogates`` so streamed text still equals the scrubbed final.
_REPLACEMENT = "\ufffd"

_SURROGATE = re.compile("[\ud800-\udfff]")


def scrub_surrogates(text: str) -> str:
    """Make ``text`` strictly UTF-8 encodable: join adjacent surrogate
    halves into their astral char, replace unpaired halves with U+FFFD.

    ``json.loads`` keeps a lone ``\\uD8XX`` escape as a lone surrogate;
    every ``ensure_ascii=False`` writer then dies on it with
    ``'utf-8' codec can't encode ... surrogates not allowed``.
    """
    if text.isascii() or _SURROGATE.search(text) is None:
        return text
    return text.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def scrub_json_strings(value: Any) -> Any:
    """``scrub_surrogates`` applied to every string (keys included) of a
    decoded JSON value; other values pass through untouched."""
    # Unchanged subtrees are returned as the SAME object (relies on
    # scrub_surrogates returning its input unchanged when it is clean), so
    # the common no-surrogate case never copies the argument tree.
    if isinstance(value, str):
        return scrub_surrogates(value)
    if isinstance(value, dict):
        items = [(scrub_json_strings(k), scrub_json_strings(v)) for k, v in value.items()]
        if all(nk is k and nv is v for (nk, nv), (k, v) in zip(items, value.items())):
            return value
        return dict(items)
    if isinstance(value, list):
        scrubbed = [scrub_json_strings(v) for v in value]
        if all(n is o for n, o in zip(scrubbed, value)):
            return value
        return scrubbed
    return value


class SurrogateJoiner:
    """Joins a UTF-16 surrogate pair split across two stream chunks.

    Providers that slice text by UTF-16 code unit can end one chunk on a
    high surrogate and start the next on its low half; each chunk decodes
    to a lone surrogate. Scrubbing chunks independently would turn a valid
    emoji into two U+FFFD, so a trailing high half is held back and
    prepended to the next chunk; ``flush`` at stream end surfaces a
    never-completed half as U+FFFD.
    """

    def __init__(self) -> None:
        self._pending = ""

    def feed(self, text: str) -> str:
        text = self._pending + text
        self._pending = ""
        if text and "\ud800" <= text[-1] <= "\udbff":
            self._pending = text[-1]
            text = text[:-1]
        return scrub_surrogates(text)

    def flush(self) -> str:
        pending, self._pending = self._pending, ""
        return _REPLACEMENT if pending else ""


@dataclass(frozen=True)
class FieldDelta:
    """One newly-safe fragment of a declared argument field."""

    call_index: int
    field_path: str
    text: str


_ESCAPES = {
    '"': '"',
    "\\": "\\",
    "/": "/",
    "b": "\b",
    "f": "\f",
    "n": "\n",
    "r": "\r",
    "t": "\t",
}


class StreamingArgExtractor:
    """Incremental extractor for one tool call's argument stream.

    Create one per ``tool_use_start``; ``feed`` raw JSON fragments in
    arrival order; ``finalize`` reconciles against the complete
    arguments so streamed text always equals the final value (a locked
    invariant).
    """

    def __init__(self, call_index: int, streamable_fields: tuple[str, ...]) -> None:
        self._call_index = call_index
        self._fields = frozenset(streamable_fields)
        self._emitted: dict[str, str] = {}
        self._aborted = False
        # Tokenizer state.
        self._stack: list[str] = []        # container stack of "{" / "["
        self._in_string = False
        self._string_is_key = False
        self._current_key: list[str] = []
        self._last_key = ""
        self._streaming_field: str | None = None
        self._escape = False
        self._unicode_hex: list[str] | None = None  # collecting \uXXXX digits
        # High half of a UTF-16 surrogate pair awaiting its low half.
        # Providers that ASCII-escape tool arguments (MiniMax) send every
        # astral char (emoji) as ``\ud83d\udc4b``; decoding each escape
        # alone yields lone surrogates that crash any strict UTF-8 encode
        # downstream (event log, stdout pipe, NarraMessenger send).
        self._pending_high: int | None = None
        self._expect_key = False

    @property
    def active(self) -> bool:
        """Whether any declared field exists (a no-op extractor otherwise)."""
        return bool(self._fields) and not self._aborted

    def feed(self, fragment: str) -> list[FieldDelta]:
        """Consume one raw fragment; return safely-decoded field deltas."""
        if not self.active or not fragment:
            return []
        out: list[str] = []
        deltas: list[FieldDelta] = []

        def flush() -> None:
            if out and self._streaming_field is not None:
                text = "".join(out)
                self._emitted[self._streaming_field] = (
                    self._emitted.get(self._streaming_field, "") + text
                )
                deltas.append(FieldDelta(self._call_index, self._streaming_field, text))
                out.clear()

        for ch in fragment:
            if self._in_string:
                self._consume_string_char(ch, out)
                if not self._in_string:
                    # String just closed.
                    if self._string_is_key:
                        self._last_key = "".join(self._current_key)
                    else:
                        flush()
                        self._streaming_field = None
                continue

            # Structural characters outside strings.
            if ch == '"':
                self._in_string = True
                self._escape = False
                self._unicode_hex = None
                self._string_is_key = self._expect_key
                if self._string_is_key:
                    self._current_key = []
                elif len(self._stack) == 1 and self._last_key in self._fields:
                    self._streaming_field = self._last_key
                continue
            if ch == "{":
                self._stack.append("{")
                self._expect_key = True
                continue
            if ch == "[":
                self._stack.append("[")
                self._expect_key = False
                continue
            if ch in "}]":
                if self._stack:
                    self._stack.pop()
                self._expect_key = False
                continue
            if ch == ":":
                self._expect_key = False
                continue
            if ch == ",":
                self._expect_key = bool(self._stack) and self._stack[-1] == "{"
                continue
            # Whitespace / literals / numbers: no state change.
        flush()
        return deltas

    def finalize(self, complete_args: dict) -> list[FieldDelta]:
        """Reconcile: emit the conservative remainder per field so that
        streamed text == final value even when escapes forced buffering."""
        if not self.active:
            return []
        deltas: list[FieldDelta] = []
        for field in sorted(self._fields):
            final = complete_args.get(field)
            if not isinstance(final, str):
                continue
            final = scrub_surrogates(final)
            emitted = self._emitted.get(field, "")
            if final.startswith(emitted) and len(final) > len(emitted):
                remainder = final[len(emitted):]
                self._emitted[field] = final
                deltas.append(FieldDelta(self._call_index, field, remainder))
        return deltas

    def abort(self) -> None:
        """Mid-stream death (cancel/deny/stream break): stop emitting;
        the consumer marks the presented prefix errored — the same
        semantics as any interrupted stream."""
        self._aborted = True

    # -- internals ----------------------------------------------------

    def _consume_string_char(self, ch: str, out: list[str]) -> None:
        """Advance in-string state by one character (escape-aware)."""
        if self._unicode_hex is not None:
            self._unicode_hex.append(ch)
            if len(self._unicode_hex) == 4:
                try:
                    code = int("".join(self._unicode_hex), 16)
                except ValueError:
                    code = None
                self._unicode_hex = None
                if code is None:
                    self._flush_pending_high(out)
                elif 0xD800 <= code <= 0xDBFF:
                    self._flush_pending_high(out)
                    self._pending_high = code
                elif 0xDC00 <= code <= 0xDFFF and self._pending_high is not None:
                    high, self._pending_high = self._pending_high, None
                    self._emit_char(
                        chr(0x10000 + ((high - 0xD800) << 10) + (code - 0xDC00)), out
                    )
                elif 0xDC00 <= code <= 0xDFFF:
                    self._emit_char(_REPLACEMENT, out)
                else:
                    self._flush_pending_high(out)
                    self._emit_char(chr(code), out)
            return
        if self._escape:
            self._escape = False
            if ch == "u":
                self._unicode_hex = []
                return
            self._flush_pending_high(out)
            self._emit_char(_ESCAPES.get(ch, ch), out)
            return
        if ch == "\\":
            self._escape = True
            return
        self._flush_pending_high(out)
        if ch == '"':
            self._in_string = False
            return
        self._emit_char(ch, out)

    def _flush_pending_high(self, out: list[str]) -> None:
        """A high surrogate not followed by its low half is unencodable:
        surface U+FFFD instead of a lone surrogate."""
        if self._pending_high is not None:
            self._pending_high = None
            self._emit_char(_REPLACEMENT, out)

    def _emit_char(self, ch: str, out: list[str]) -> None:
        if self._string_is_key:
            self._current_key.append(ch)
        elif self._streaming_field is not None:
            out.append(ch)
