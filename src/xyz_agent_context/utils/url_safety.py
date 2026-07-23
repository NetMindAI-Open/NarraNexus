"""
@file_name: url_safety.py
@author: Bin Liang
@date: 2026-07-22
@description: SSRF-safety primitive for server-side outbound HTTP.

Any time the SERVER fetches a user- or agent-supplied URL (the URL-tab embed
probe today; the headless RenderService / streaming browser later), it can be
tricked into hitting internal services — the classic SSRF vector, and on
EC2 the cloud metadata endpoint `169.254.169.254`. This module is the single
gate every such fetch must pass through.

Deliberately generic and dependency-light (stdlib only, injectable resolver)
so it lives in `utils/` with no artifact coupling — the RenderService and the
streaming browser will import the same function.

Note the trust boundary: this guards requests WE originate server-side. An
`<iframe src>` is fetched by the USER's browser, not us, so it is not on this
SSRF surface — but open_url still validates the initial URL here to refuse
obviously-internal targets early.
"""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Awaitable, Callable, List, Optional
from urllib.parse import urlparse

ALLOWED_SCHEMES = frozenset({"http", "https"})

# (host, port) -> list of resolved IP strings. Injectable so tests need no
# real DNS and the future RenderService can share a cached resolver.
Resolver = Callable[[str, int], Awaitable[List[str]]]


class UnsafeUrlError(ValueError):
    """Raised when a URL is not a safe public HTTP(S) target."""


def _is_public_ip(ip_str: str) -> bool:
    """True only for globally-routable addresses. Rejects private, loopback,
    link-local (covers the 169.254.169.254 metadata IP), reserved,
    multicast, and unspecified ranges — for both IPv4 and IPv6."""
    ip = ipaddress.ip_address(ip_str)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


async def _default_resolver(host: str, port: int) -> List[str]:
    loop = asyncio.get_event_loop()
    infos = await loop.getaddrinfo(host, port, proto=0)
    # sockaddr is info[4]; its first element is the address string (typed
    # str|int by the stubs because ports are ints — coerce to str).
    return [str(info[4][0]) for info in infos]


async def assert_public_http_url(
    url: str,
    *,
    resolver: Optional[Resolver] = None,
) -> List[str]:
    """Validate `url` is a safe public HTTP(S) target, or raise UnsafeUrlError.

    Resolves the host and rejects if ANY resolved address is non-public
    (this is what defeats DNS-rebinding — validation happens post-resolution,
    on the actual addresses a client would connect to). A literal-IP host is
    checked directly without resolving.

    Returns the list of validated public IPs (useful for connection pinning
    by a future caller). Raises UnsafeUrlError on any failure.
    """
    parsed = urlparse(url)
    if parsed.scheme not in ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"scheme {parsed.scheme!r} is not http/https")
    host = parsed.hostname
    if not host:
        raise UnsafeUrlError("URL has no host")

    # Literal IP host — validate directly, no DNS. The public-ness check lives
    # in the `else` (runs only when ip_address did NOT raise), NOT inside the
    # try: UnsafeUrlError subclasses ValueError, so a `raise` inside the try
    # would be swallowed by `except ValueError`, making the literal-IP
    # rejection dead code that silently falls through to DNS.
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # not a literal IP — it's a hostname, resolve it below
    else:
        if not _is_public_ip(host):
            raise UnsafeUrlError(f"host {host!r} is not a public address")
        return [host]

    default_port = 443 if parsed.scheme == "https" else 80
    resolve = resolver or _default_resolver
    try:
        addresses = await resolve(host, parsed.port or default_port)
    except UnsafeUrlError:
        raise
    except Exception as e:  # noqa: BLE001 — DNS failure is a hard reject, not a pass
        raise UnsafeUrlError(f"could not resolve host {host!r}: {e}") from e

    if not addresses:
        raise UnsafeUrlError(f"host {host!r} resolved to no addresses")
    for addr in addresses:
        if not _is_public_ip(addr):
            raise UnsafeUrlError(f"host {host!r} resolves to non-public address {addr}")
    return addresses
