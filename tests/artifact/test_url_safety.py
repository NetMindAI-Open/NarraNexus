"""
@file_name: test_url_safety.py
@author: Bin Liang
@date: 2026-07-22
@description: SSRF-gate tests for utils/url_safety.assert_public_http_url.

Uses an injected resolver so no real DNS is hit. Covers each rejected class
(bad scheme, no host, literal private/loopback/link-local/metadata IP,
hostname resolving to a private address, DNS-rebinding where one of several
addresses is internal) and the accepted public cases.
"""
from __future__ import annotations

import pytest

from xyz_agent_context.utils.url_safety import (
    UnsafeUrlError,
    assert_public_http_url,
    is_obviously_non_public_host,
)


def _resolver_returning(*addresses):
    async def _r(host, port):
        return list(addresses)
    return _r


@pytest.mark.asyncio
async def test_public_hostname_accepted():
    ips = await assert_public_http_url(
        "https://example.com/path", resolver=_resolver_returning("93.184.216.34")
    )
    assert ips == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_public_literal_ip_accepted_without_resolving():
    # No resolver needed — literal public IP is validated directly.
    ips = await assert_public_http_url("http://93.184.216.34:8080/")
    assert ips == ["93.184.216.34"]


@pytest.mark.asyncio
@pytest.mark.parametrize("url", [
    "ftp://example.com",
    "file:///etc/passwd",
    "gopher://example.com",
    "javascript:alert(1)",
])
async def test_non_http_scheme_rejected(url):
    with pytest.raises(UnsafeUrlError):
        await assert_public_http_url(url, resolver=_resolver_returning("93.184.216.34"))


@pytest.mark.asyncio
@pytest.mark.parametrize("host", [
    "127.0.0.1",       # loopback
    "10.0.0.5",        # private A
    "192.168.1.1",     # private C
    "172.16.0.1",      # private B
    "169.254.169.254", # cloud metadata (link-local)
    "0.0.0.0",         # unspecified
    "[::1]",           # ipv6 loopback
])
async def test_literal_internal_ip_rejected(host):
    with pytest.raises(UnsafeUrlError):
        await assert_public_http_url(f"http://{host}/")


@pytest.mark.asyncio
async def test_literal_internal_ip_rejected_even_with_permissive_resolver():
    # Regression: the literal-IP rejection must NOT fall through to the
    # resolver. A resolver that lies (returns a public IP for everything) must
    # not let a literal metadata IP through — the literal branch decides first.
    with pytest.raises(UnsafeUrlError):
        await assert_public_http_url(
            "http://169.254.169.254/", resolver=_resolver_returning("93.184.216.34"),
        )


@pytest.mark.asyncio
async def test_hostname_resolving_to_private_rejected():
    with pytest.raises(UnsafeUrlError):
        await assert_public_http_url(
            "http://sneaky.internal/", resolver=_resolver_returning("10.1.2.3")
        )


@pytest.mark.asyncio
async def test_dns_rebinding_one_bad_address_rejects_all():
    # If ANY resolved address is internal, the whole URL is rejected — a host
    # that returns [public, private] must not slip through.
    with pytest.raises(UnsafeUrlError):
        await assert_public_http_url(
            "http://rebind.example/",
            resolver=_resolver_returning("93.184.216.34", "127.0.0.1"),
        )


@pytest.mark.asyncio
async def test_metadata_ip_rejected_even_via_hostname():
    with pytest.raises(UnsafeUrlError):
        await assert_public_http_url(
            "http://metadata.evil/", resolver=_resolver_returning("169.254.169.254")
        )


@pytest.mark.asyncio
async def test_resolution_failure_is_hard_reject():
    async def _boom(host, port):
        raise OSError("dns down")
    with pytest.raises(UnsafeUrlError):
        await assert_public_http_url("http://whatever.example/", resolver=_boom)


@pytest.mark.asyncio
async def test_empty_resolution_rejected():
    with pytest.raises(UnsafeUrlError):
        await assert_public_http_url("http://void.example/", resolver=_resolver_returning())


# --- is_obviously_non_public_host (sync config screen, no DNS) --------------
#
# Separate question from the SSRF gate above: that one guards attacker-supplied
# URLs the SERVER fetches; this one screens a CONFIGURED origin before we hand it
# to a third-party API. First caller: routes/billing._return_urls, where a
# loopback/private value earns an HTML 403 from NetMind's edge and would break
# the user's checkout.


@pytest.mark.parametrize("host", [
    "localhost",
    "LOCALHOST",           # case-insensitive
    "foo.localhost",
    "printer.local",       # mDNS
    "my-nas",              # single-label name — never publicly resolvable
    "127.0.0.1",
    "10.0.0.5",
    "192.168.1.50",
    "172.16.0.1",
    "169.254.169.254",     # cloud metadata
    "::1",
    "fe80::1",
    "0.0.0.0",
    "",
    "   ",
])
def test_non_public_hosts_are_flagged(host):
    assert is_obviously_non_public_host(host) is True


@pytest.mark.parametrize("host", [
    "agent.narra.nexus",
    "example.com",
    "agent.narra.nexus.",  # trailing root dot is still the same public name
    "8.8.8.8",
    "2001:4860:4860::8888",
])
def test_public_hosts_pass(host):
    assert is_obviously_non_public_host(host) is False
