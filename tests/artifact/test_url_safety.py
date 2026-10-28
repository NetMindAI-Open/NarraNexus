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

from narranexus.platform.utils.url_safety import (
    UnsafeUrlError,
    assert_public_http_url,
    assert_public_http_url_in_cloud,
    is_obviously_non_public_host,
    is_obviously_non_public_url,
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


# --- is_obviously_non_public_url (parse-safe URL screen, no DNS) ------------

@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8000/x",
        "http://localhost:3845/mcp",
        "http://narranexus-litellm:4000/mcp",
        # parse failures / garbage MUST be treated as non-public, never raise:
        "http://[::1",   # urlparse -> ValueError (bad IPv6)
        123,             # non-string -> AttributeError inside urlparse
        None,            # -> empty host -> non-public
        "",              # empty
    ],
)
def test_non_public_or_unparseable_urls_are_flagged(url):
    assert is_obviously_non_public_url(url) is True


@pytest.mark.parametrize(
    "url",
    [
        "https://api.example.com/sse",
        "http://8.8.8.8/mcp",
        "https://frps.example.com:6027/sse",
    ],
)
def test_public_urls_pass(url):
    assert is_obviously_non_public_url(url) is False


# --- assert_public_http_url_in_cloud (deployment-aware wrapper) -------------


@pytest.mark.asyncio
async def test_in_cloud_wrapper_enforces_the_gate_in_cloud(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    with pytest.raises(UnsafeUrlError):
        await assert_public_http_url_in_cloud(
            "http://llm-gateway:4000/v1", resolver=_resolver_returning("172.20.0.18")
        )
    with pytest.raises(UnsafeUrlError):
        await assert_public_http_url_in_cloud("http://169.254.169.254/latest")
    # A public target still passes in cloud.
    await assert_public_http_url_in_cloud(
        "https://api.example.com/v1", resolver=_resolver_returning("93.184.216.34")
    )


@pytest.mark.asyncio
async def test_in_cloud_wrapper_is_a_no_op_locally(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")

    async def _resolver_must_not_run(host, port):
        raise AssertionError("local mode must not resolve")

    # Local/desktop: a single trusted user legitimately targets localhost.
    await assert_public_http_url_in_cloud(
        "http://localhost:11434/v1", resolver=_resolver_must_not_run
    )
    await assert_public_http_url_in_cloud("http://169.254.169.254/latest")


@pytest.mark.asyncio
async def test_in_cloud_wrapper_returns_the_validated_addresses_in_cloud(monkeypatch):
    """The wrapper hands back the gate's validated IPs (for connection pinning)."""
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    addresses = await assert_public_http_url_in_cloud(
        "https://api.example.com/v1", resolver=_resolver_returning("93.184.216.34")
    )
    assert addresses == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_in_cloud_wrapper_returns_none_locally_meaning_allowed(monkeypatch):
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "local")
    assert await assert_public_http_url_in_cloud("http://localhost:11434/v1") is None
