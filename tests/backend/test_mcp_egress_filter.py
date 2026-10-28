"""
@file_name: test_mcp_egress_filter.py
@date: 2026-08-11
@description: Runtime SSRF egress filter (security audit P0-3, review round 2).

The enabled MCP URLs are fetched at agent runtime and their responses enter the
model context, so in CLOUD mode any server resolving to an internal address is
dropped before the run. LOCAL mode passes everything through (localhost MCP is
a first-class desktop use). Uses literal IPs so no real DNS is needed.
"""
from __future__ import annotations

import pytest

import backend.routes._mcp_egress as egress


@pytest.mark.asyncio
async def test_local_mode_passthrough(monkeypatch):
    monkeypatch.setattr(egress, "is_cloud_mode", lambda: False)
    servers = {
        "local": {"url": "http://127.0.0.1:3845/mcp"},
        "public": {"url": "http://8.8.8.8/mcp"},
    }
    assert await egress.filter_public_mcp_servers(servers) == servers


@pytest.mark.asyncio
async def test_cloud_drops_internal_keeps_public(monkeypatch):
    monkeypatch.setattr(egress, "is_cloud_mode", lambda: True)
    servers = {
        "pub": {"url": "http://8.8.8.8/mcp"},              # literal public
        "imds": {"url": "http://169.254.169.254/x"},       # link-local
        "loop": {"url": "http://127.0.0.1:8000/x"},        # loopback
        "priv": {"url": "http://10.0.0.9:4000/sse"},       # RFC1918
    }
    result = await egress.filter_public_mcp_servers(servers)
    assert set(result.keys()) == {"pub"}


@pytest.mark.asyncio
async def test_cloud_fail_closed_on_malformed_url(monkeypatch):
    """A malformed URL (urlparse raises a bare ValueError, not UnsafeUrlError)
    must be DROPPED, not left in the set — otherwise one bad row disables the
    gate for the whole batch."""
    monkeypatch.setattr(egress, "is_cloud_mode", lambda: True)
    servers = {
        "malformed": {"url": "http://[::1"},           # urlparse -> ValueError
        "imds": {"url": "http://169.254.169.254/x"},    # internal
        "pub": {"url": "http://8.8.8.8/mcp"},           # literal public
    }
    result = await egress.filter_public_mcp_servers(servers)
    assert set(result.keys()) == {"pub"}


@pytest.mark.asyncio
async def test_empty_is_noop(monkeypatch):
    monkeypatch.setattr(egress, "is_cloud_mode", lambda: True)
    assert await egress.filter_public_mcp_servers({}) == {}
