"""
@file_name: safe_http.py
@author: Bin Liang
@date: 2026-07-22
@description: Server-side HTTP GET that follows redirects with an SSRF gate on
every hop.

The ONE place the redirect-walk lives, so the security-critical per-hop SSRF
check has a single implementation (embed probe, page-text extraction, and the
future RenderService / streaming browser all share it — a second copy of this
loop is a place to forget the gate). Kept separate from `url_safety.py` so
that module stays stdlib-only (its stated contract); this one owns the httpx
dependency.

`safe_stream_get` is an async context manager yielding the FINAL response with
its body still streamable — the caller decides whether to read headers only
(probe) or a bounded slice of the body (text extraction).
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import AsyncIterator, Optional
from urllib.parse import urljoin

import httpx

from xyz_agent_context.utils.url_safety import Resolver, assert_public_http_url

# A realistic desktop browser UA — some sites vary behaviour by UA, and we want
# what a real browser fetch would see. Single source for every safe fetch.
BROWSER_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)
MAX_REDIRECTS = 5


class RedirectLimitError(Exception):
    """More than MAX_REDIRECTS hops — treated as a fetch failure by callers."""


@asynccontextmanager
async def safe_stream_get(
    client: httpx.AsyncClient,
    url: str,
    *,
    resolver: Optional[Resolver] = None,
    user_agent: str = BROWSER_UA,
    max_redirects: int = MAX_REDIRECTS,
) -> AsyncIterator[httpx.Response]:
    """Stream a GET, following redirects manually with a per-hop SSRF gate.

    Yields the final (non-redirect) response with its body unread, so the
    caller can read headers only or a bounded body slice. The `client` MUST be
    configured with `follow_redirects=False` (we follow manually so every hop
    passes `assert_public_http_url`).

    Raises:
        UnsafeUrlError: any hop (including the first) points at a non-public
            address — propagated so the caller can degrade.
        RedirectLimitError: exceeded `max_redirects`.
    """
    current = url
    for _ in range(max_redirects + 1):
        await assert_public_http_url(current, resolver=resolver)
        async with client.stream("GET", current, headers={"User-Agent": user_agent}) as resp:
            if resp.is_redirect and "location" in resp.headers:
                current = urljoin(current, resp.headers["location"])
                continue
            yield resp
            return
    raise RedirectLimitError(f"exceeded {max_redirects} redirects for {url}")
