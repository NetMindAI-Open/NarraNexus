"""
@file_name: page_text.py
@author: Bin Liang
@date: 2026-07-22
@description: Best-effort readable-text extraction for a URL tab.

When a URL tab is opened, the agent should be able to SEE what the page says —
not just that a tab exists. This fetches the page server-side and extracts a
bounded plain-text snapshot; `open_url` writes it into the tab's workspace dir
(`content.md`) so the agent can Read it. Visual (rendered-screenshot) vision
is a separate, heavier capability (the RenderService, i.e. the streaming-browser plan) — this is the
cheap text-level answer.

Safety / bounds (this is a server-side fetch of a user/agent-supplied URL):
- Every hop passes the SSRF gate ([[url_safety.py]]); redirects are followed
  manually so an internal redirect target is caught.
- The body read is HARD-CAPPED (`_MAX_FETCH_BYTES`) — we stop reading mid-
  stream, never pulling an unbounded body into memory.
- Only text/html (or text/*) is extracted; other content types return None.
- Output is capped (`_MAX_TEXT_CHARS`). Never raises — a failure returns None,
  and the caller falls back to a "text unavailable" note.
"""

from __future__ import annotations

import re
from html import unescape
from typing import Optional

import httpx
from loguru import logger

from xyz_agent_context.utils.safe_http import safe_stream_get
from xyz_agent_context.utils.url_safety import Resolver

_TIMEOUT_S = 6.0
_MAX_FETCH_BYTES = 1_000_000  # read at most ~1 MB of body
_MAX_TEXT_CHARS = 16_000      # cap the extracted text handed to the agent

# Comments first — a commented-out `<script>`/`<style>` (common) would
# otherwise look like an orphan open tag to the dangling-strip below, and a
# raw `<!--` leaks into the text if left in.
_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
# Complete blocks. `</\1\s*>` tolerates a space before `>` (`</script >`).
_SCRIPT_STYLE = re.compile(r"<(script|style|noscript|template)\b[^>]*>.*?</\1\s*>", re.IGNORECASE | re.DOTALL)
# An OPEN script/style tag on its own — after complete blocks are removed, any
# remaining match is an ORPHAN (unclosed). Matches only the tag, not what
# follows, so we can locate orphans without eating text.
_OPEN_SCRIPT_STYLE = re.compile(r"<(?:script|style|noscript|template)\b[^>]*>", re.IGNORECASE)
_BLOCK_END = re.compile(r"</(p|div|section|article|li|tr|h[1-6]|header|footer)\s*>|<br\s*/?>", re.IGNORECASE)
_TAG = re.compile(r"<[^>]+>")
_INLINE_WS = re.compile(r"[ \t\r\f\v]+")
_MULTI_NL = re.compile(r"\n[ \t]*\n[ \t]*(?:\n\s*)+")


def html_to_text(html: str) -> str:
    """Crude, dependency-free HTML → readable text. Not a full parser — good
    enough to give the agent the gist of a page's content."""
    s = _COMMENT.sub(" ", html)
    s = _SCRIPT_STYLE.sub(" ", s)
    # A byte-capped body can leave an UNCLOSED script/style at the tail (its
    # `</style>` was never read). Cut at the LAST orphan open tag only — this
    # is by definition a document-tail phenomenon, so anchoring on the last
    # orphan loses just the truncated tail, never middle body after some
    # orphan the complete-block regex happened to miss.
    opens = list(_OPEN_SCRIPT_STYLE.finditer(s))
    if opens:
        s = s[: opens[-1].start()]
    s = _BLOCK_END.sub("\n", s)  # turn block-ends into line breaks
    s = _TAG.sub("", s)
    s = unescape(s)
    s = _INLINE_WS.sub(" ", s)
    s = _MULTI_NL.sub("\n\n", s)
    return s.strip()


async def fetch_page_text(
    url: str,
    *,
    resolver: Optional[Resolver] = None,
    client: Optional[httpx.AsyncClient] = None,
) -> Optional[str]:
    """Fetch `url` and return a bounded plain-text snapshot, or None.

    Best-effort: any failure (network, SSRF on a hop, non-HTML, empty) returns
    None rather than raising, so it never breaks tab creation.
    """
    owns_client = client is None
    if client is None:
        client = httpx.AsyncClient(follow_redirects=False, timeout=_TIMEOUT_S)
    try:
        # Redirect-following + per-hop SSRF gate live in the shared
        # safe_stream_get; here we just read a BOUNDED slice of the body.
        async with safe_stream_get(client, url, resolver=resolver) as resp:
            ctype = resp.headers.get("content-type", "").lower()
            if "html" not in ctype and "text/" not in ctype:
                return None  # not a text page — nothing to extract
            consumed = 0
            chunks: list[bytes] = []
            async for chunk in resp.aiter_bytes():
                chunks.append(chunk)
                consumed += len(chunk)
                if consumed >= _MAX_FETCH_BYTES:
                    break  # hard stop — never pull an unbounded body into memory
            raw = b"".join(chunks).decode(resp.encoding or "utf-8", errors="replace")
            text = html_to_text(raw)[:_MAX_TEXT_CHARS].strip()
            return text or None
    except Exception as e:  # noqa: BLE001 — best-effort; any failure (network,
        # SSRF hop, redirect limit, non-HTML) just means no text, never a crash.
        logger.info("page_text: fetch failed for {}: {}", url, e)
        return None
    finally:
        if owns_client:
            await client.aclose()
