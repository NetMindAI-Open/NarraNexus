"""
@file_name: test_mcp_repository_ssrf.py
@date: 2026-08-11
@description: SSRF gate on MCP URL validation and storage — and its cloud/local
mode gating (铁律 #7).

In CLOUD mode a user-/agent-supplied MCP URL that resolves internal (cloud
metadata, loopback, docker network) must be refused, including the DNS-
rebinding case. In LOCAL/desktop mode the gate is OFF: a single trusted user
legitimately runs MCP servers on localhost, so `localhost` must NOT be blocked.
The deployment-mode decision lives in the route layer, which passes
`enforce_public_url=` to the repository and cloud-gates the store-time screen.
"""
from __future__ import annotations

import pytest

from narranexus.platform.repository.mcp_repository import validate_mcp_sse_connection
import backend.routes.agents.mcps as mcps_route


# ── connect-time gate (enforced = cloud) ──────────────────────────────

@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://169.254.169.254/latest/meta-data/",  # EC2 IMDS (link-local)
        "http://127.0.0.1:8000/api/admin/",           # loopback
        "http://10.0.0.5:4000/",                       # private (RFC1918)
        "http://192.168.1.10/sse",                     # private
        "https://[::1]/sse",                           # IPv6 loopback
    ],
)
async def test_validate_rejects_internal_when_enforced(url):
    """enforce_public_url=True (cloud): a literal internal-IP target is rejected
    without any network call."""
    ok, error = await validate_mcp_sse_connection(url)  # default enforce=True
    assert ok is False
    assert error and "not allowed" in error.lower()


@pytest.mark.asyncio
async def test_validate_rejects_dns_rebinding_when_enforced():
    """A public-looking hostname that resolves to an internal address is
    rejected — the post-resolution check narrows the rebinding window."""
    async def _internal_resolver(host, port):
        return ["169.254.169.254"]

    ok, error = await validate_mcp_sse_connection(
        "http://totally-legit.example.com/sse", resolver=_internal_resolver
    )
    assert ok is False
    assert error and "not allowed" in error.lower()


@pytest.mark.asyncio
async def test_validate_local_mode_skips_ssrf_gate(monkeypatch):
    """enforce_public_url=False (local/desktop): the gate is skipped, so a
    localhost MCP URL is NOT rejected as 'not allowed' — it proceeds to
    connect."""
    import httpx

    reached = {"v": False}

    class _Resp:
        status_code = 200
        headers = {"content-type": "text/event-stream"}

        async def aiter_bytes(self):
            yield b"data: ok\n\n"

    class _Ctx:
        async def __aenter__(self):
            return _Resp()

        async def __aexit__(self, *a):
            return False

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, m, u, headers=None):
            reached["v"] = True
            return _Ctx()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)
    ok, error = await validate_mcp_sse_connection(
        "http://localhost:7801/sse", enforce_public_url=False
    )
    assert ok is True
    assert reached["v"] is True  # got past the gate to the connection
    assert "not allowed" not in (error or "").lower()


# ── store-time screen (cloud-gated) ───────────────────────────────────

@pytest.mark.parametrize(
    "url,blocked",
    [
        ("http://169.254.169.254/x", True),
        ("http://127.0.0.1/x", True),
        ("http://localhost/x", True),
        ("http://narranexus-litellm:4000/mcp", True),  # single-label docker name
        ("http://foo.local/sse", True),                 # mDNS
        ("https://api.example.com/sse", False),         # public host
        ("https://frps.example.com:6027/sse", False),   # public host
    ],
)
def test_store_time_screen_blocks_in_cloud(monkeypatch, url, blocked):
    monkeypatch.setattr(mcps_route, "is_cloud_mode", lambda: True)
    assert mcps_route._blocks_internal_url(url) is blocked


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:3845/mcp",   # standard local MCP bridge
        "http://127.0.0.1/x",
        "http://narranexus-litellm:4000/mcp",
        "https://api.example.com/sse",
    ],
)
def test_store_time_screen_allows_everything_in_local(monkeypatch, url):
    """Local mode never blocks — localhost MCP is a first-class desktop use."""
    monkeypatch.setattr(mcps_route, "is_cloud_mode", lambda: False)
    assert mcps_route._blocks_internal_url(url) is False


# ── error text never echoes upstream body / exception detail ──────────
# The returned message is written into mcp_urls.last_error and shown to the
# user, so any upstream content in it turns a blind fetch into a readable one.

UPSTREAM_SECRET = "UPSTREAM-SECRET-imds-AccessKeyId"


async def _public_resolver(host, port):
    return ["93.184.216.34"]


def _install_fake_httpx(monkeypatch, *, status=500, body=b"", raise_exc=None):
    import httpx

    class _Resp:
        status_code = status
        headers = {"content-type": "application/json"}

        async def aiter_bytes(self):
            yield body

    class _Ctx:
        async def __aenter__(self):
            if raise_exc is not None:
                raise raise_exc
            return _Resp()

        async def __aexit__(self, *a):
            return False

    class _Client:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url, headers=None):
            return _Ctx()

    monkeypatch.setattr(httpx, "AsyncClient", _Client)


@pytest.mark.asyncio
async def test_validate_error_status_does_not_echo_body(monkeypatch):
    _install_fake_httpx(monkeypatch, status=500, body=UPSTREAM_SECRET.encode())

    ok, error = await validate_mcp_sse_connection(
        "https://mcp.example.com/sse", resolver=_public_resolver
    )

    assert ok is False
    assert "500" in error
    assert UPSTREAM_SECRET not in error


@pytest.mark.asyncio
@pytest.mark.parametrize("exc_kind", ["connect", "other"])
async def test_validate_exception_does_not_echo_detail(monkeypatch, exc_kind):
    import httpx

    detail = f"connect to 172.20.0.7:8080 refused {UPSTREAM_SECRET}"
    exc = httpx.ConnectError(detail) if exc_kind == "connect" else RuntimeError(detail)
    _install_fake_httpx(monkeypatch, raise_exc=exc)

    ok, error = await validate_mcp_sse_connection(
        "https://mcp.example.com/sse", resolver=_public_resolver
    )

    assert ok is False
    assert UPSTREAM_SECRET not in error
    assert "172.20" not in error
