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

Deliberately generic and dependency-light (stdlib plus the env-only
`deployment_mode` leaf, injectable resolver) so it lives in `utils/` with no
artifact coupling — the RenderService and the streaming browser will import the
same function.

Deployment awareness lives HERE too: `assert_public_http_url_in_cloud` is the
one place that decides "cloud enforces, local/desktop allows". Sinks whose
targets are user-supplied (provider base_url, transcription endpoint, Home
Assistant, NarraMessenger bind) call it and only translate a refusal into their
own return contract and user-facing text — they never re-implement the
cloud check.

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

from narranexus.platform.utils import deployment_mode

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


def is_obviously_non_public_host(host: str) -> bool:
    """True when `host` cannot be a public internet name, decided WITHOUT DNS.

    A cheap synchronous screen for CONFIG values — NOT a security boundary. The
    SSRF gate for attacker-supplied URLs we fetch ourselves is
    `assert_public_http_url`; this answers a different question: "is it safe to
    hand this configured origin to a third party?"

    Catches what an operator actually types: a literal private/loopback/
    link-local IP, `localhost`, an mDNS `.local` name, and single-label
    hostnames like `my-nas` — none of which exist on the public internet.

    Deliberately no DNS: the caller decides whether to send a URL to an external
    API, and a DNS hiccup must not flip that decision. So a False result means
    "not obviously private", never "verified public".
    """
    host = host.strip().lower().rstrip(".")
    if not host:
        return True
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass  # a hostname, screened below
    else:
        return not _is_public_ip(host)
    if host == "localhost" or host.endswith(".localhost"):
        return True
    if host.endswith(".local"):  # mDNS / Bonjour
        return True
    return "." not in host  # single-label name is never publicly resolvable


def is_obviously_non_public_url(url: object) -> bool:
    """`is_obviously_non_public_host` for a whole URL — parse-safe.

    Extracts the host and applies the same DNS-free screen, but ALSO treats an
    unparseable or non-string URL as non-public (returns True). A screen over
    attacker-supplied input must never crash the caller (a malformed
    ``http://[::1`` raises ValueError; a non-string like ``123`` raises
    AttributeError) — "can't parse it" is decided as "not safe", same posture
    as the connect-time gate's fail-closed. Use this at store sites (route
    create/update, bundle import) so a garbage URL is screened out, not thrown.
    """
    try:
        host = urlparse(url or "").hostname or ""
    except Exception:  # noqa: BLE001 — unparseable/garbage input → treat as non-public
        return True
    return is_obviously_non_public_host(host)


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


async def assert_public_http_url_in_cloud(
    url: str,
    *,
    resolver: Optional[Resolver] = None,
) -> Optional[List[str]]:
    """Deployment-aware egress gate: cloud enforces, local/desktop allows.

    Cloud: the server shares a network with the metadata endpoint and every
    internal service, so `url` must pass `assert_public_http_url` (raises
    UnsafeUrlError — or a bare ValueError for an unparseable URL — on refusal;
    callers fail closed on any exception). Local/desktop is a single trusted
    user who legitimately points at localhost or the LAN (Ollama, LM Studio, a
    LAN Home Assistant), so it returns without resolving anything.

    Raises rather than returning a verdict: each caller owns its own failure
    contract and user-facing wording, and must never echo the exception text
    (it can contain resolved internal addresses).

    Returns the validated public IPs in cloud (for connection pinning) and
    ``None`` locally. ``None`` means ALLOWED (nothing was checked), never
    "refused": refusal is only ever signalled by raising.
    """
    if not deployment_mode.is_cloud_mode():
        return None
    return await assert_public_http_url(url, resolver=resolver)
