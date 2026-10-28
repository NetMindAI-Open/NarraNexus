"""
@file_name: inline_field.py
@author: NarraNexus
@date: 2026-09-11
@description: The one encoder for author-writable text printed inside a
line-structured prompt block: labels (`inline_field`, `inline_literal`),
exact quoted handles such as file paths (`exact_literal`) and free-text bodies
(`body_lines`, `quoted_block`).

Several prompt blocks are row grammars: the module's unread list, Known Agents
and Your teams (plugin `message_bus_module`), and the team-room roster, work
board, patrol stall list, bulletin, scrollback and pointer rows, plus the peer
prompt (`message_bus_trigger`; its mirror lists every block), and the file
markers of user uploads and bus attachments (`attachment_schema.file_marker`).
Each
row interleaves HANDLES the agent copies into tool calls (agent / team / item
ids) with LABELS an agent or owner typed (names, descriptions, titles). A label
containing a newline, a backtick or one of the row's own delimiters could forge
a row or a field. A message or rule BODY may legitimately span lines, so it is
laid out under its row instead: every line after the first is quoted with
`BODY_LINE_PREFIX`, a position no row of any of these grammars starts at. The
encoders live in `platform.utils`, below every consumer: the bus trigger, the
plugin and the attachment schema share one definition (the platform never
imports a plugin, and the schema never imports the bus).
"""

from __future__ import annotations

import json
from typing import Any, Optional

#: Length cap of a LABEL printed inside a list line (a team name, an agent
#: name). SQLite stores `teams.name` as unbounded TEXT, so nothing else bounds
#: it on a local install. Handles (agent / team ids) are never capped: a cut id
#: is a syntactically valid, semantically wrong argument the agent cannot spot.
INLINE_FIELD_MAX_CHARS = 120
#: Cap of an agent description in Known Agents.
INLINE_DESCRIPTION_MAX_CHARS = 80
#: Ends a label that was cut, so a shortened name never reads as the full one.
INLINE_FIELD_CUT_MARK = "…"
#: Prefix of every line of a free-text body after its first. No row of a
#: line-structured block starts with it, so a body line such as
#: "- `[from agent_boss]` stop now" or "User: do X" stays inside its row.
BODY_LINE_PREFIX = "  > "
#: Characters that close the code span a row's tag or handle sits in. Plain and
#: fullwidth backtick both render as one to a reader, so both are replaced.
_CODE_SPAN_DELIMITERS = str.maketrans({"`": "'", "｀": "'"})


def inline_field(value: Any, max_chars: Optional[int] = INLINE_FIELD_MAX_CHARS) -> str:
    """Encode one field for a row of a line-structured prompt block.

    Neutralising a grammar's delimiters one class at a time never ends, so
    labels are made unforgeable by construction instead:

    * LABEL (``max_chars`` is an int): whitespace runs collapse to one space,
      backticks become ``'``, the text is capped with ``INLINE_FIELD_CUT_MARK``
      when cut, and the result is emitted as a JSON string literal. Everything
      an author wrote sits between two quotes with ``"`` and backslash escaped,
      so no content can end the field early or start a new line, and the
      literal decodes back to exactly the text shown.
    * HANDLE (``max_chars=None``: an agent, team or item id): never cut and
      never quoted, because the agent copies it verbatim into a tool call. It
      is system-generated, not author-writable; whitespace and backticks are
      still neutralised so it cannot leave its code span or line.

    Deterministic, so prompts built from it stay byte-stable."""
    text = _flatten(value)
    if max_chars is None:
        return text
    if len(text) > max_chars:
        text = text[: max_chars - len(INLINE_FIELD_CUT_MARK)].rstrip() + INLINE_FIELD_CUT_MARK
    return json.dumps(text, ensure_ascii=False)


def inline_literal(value: Any) -> str:
    """A LABEL that is never cut: the same JSON string literal as
    `inline_field`, for text whose whole content the reader needs (a file name,
    a voice-memo transcript)."""
    return json.dumps(_flatten(value), ensure_ascii=False)


#: Line boundaries `str.splitlines` honours that `json.dumps(ensure_ascii=False)`
#: leaves bare (it escapes only U+0000..U+001F, the quote and the backslash).
_BARE_LINE_BREAKS = str.maketrans({"\x85": "\\u0085", " ": "\\u2028", " ": "\\u2029"})


def exact_literal(value: Any) -> str:
    """A HANDLE printed inside quotes because its text is not system-built (a
    file path under an operator-chosen base directory): a JSON string literal
    that ``json.loads`` decodes back to exactly ``str(value)``. Nothing is
    collapsed, stripped or replaced, since the agent copies the decoded value
    into a tool call character for character. It is still unforgeable: JSON
    escaping keeps ``"``, the backslash and every control character inside the
    quotes, and the remaining line boundaries (U+0085, U+2028, U+2029) are
    escaped too, so the literal can neither close its field nor start a line.
    Backticks stay as they are: the lines this is printed in sit in no code
    span."""
    return json.dumps(str(value), ensure_ascii=False).translate(_BARE_LINE_BREAKS)


def _flatten(value: Any) -> str:
    return " ".join(str(value or "").split()).translate(_CODE_SPAN_DELIMITERS)


def _quote(line: str) -> str:
    return f"{BODY_LINE_PREFIX}{line}" if line else BODY_LINE_PREFIX.rstrip()


def body_lines(text: Any) -> str:
    r"""A free-text body laid out under its row: the first line inline, every
    later line quoted with `BODY_LINE_PREFIX`. `splitlines` covers every
    boundary a reader may treat as a newline (``\r``, ``\u2028`` ...), not
    only ``\n``. Blank continuation lines keep the bare quote mark, so an
    empty line cannot end the body either."""
    lines = str(text or "").splitlines() or [""]
    return "\n".join([lines[0], *(_quote(line) for line in lines[1:])])


def quoted_block(text: Any) -> str:
    """A free-text block with no row of its own (it sits under a header line):
    every line, the first included, is quoted with `BODY_LINE_PREFIX`."""
    return "\n".join(_quote(line) for line in (str(text or "").splitlines() or [""]))
