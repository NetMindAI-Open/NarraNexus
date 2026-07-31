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

from dataclasses import dataclass


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
                    decoded = chr(int("".join(self._unicode_hex), 16))
                except ValueError:
                    decoded = ""
                self._unicode_hex = None
                self._emit_char(decoded, out)
            return
        if self._escape:
            self._escape = False
            if ch == "u":
                self._unicode_hex = []
                return
            self._emit_char(_ESCAPES.get(ch, ch), out)
            return
        if ch == "\\":
            self._escape = True
            return
        if ch == '"':
            self._in_string = False
            return
        self._emit_char(ch, out)

    def _emit_char(self, ch: str, out: list[str]) -> None:
        if self._string_is_key:
            self._current_key.append(ch)
        elif self._streaming_field is not None:
            out.append(ch)
