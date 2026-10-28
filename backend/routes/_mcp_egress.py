"""
@file_name: _mcp_egress.py
@date: 2026-08-11
@description: Cloud-only SSRF egress filter for user-configured MCP servers.

The enabled MCP URLs a user stores are fetched at agent RUNTIME — their
response bodies land in the model's context — so a URL that resolves to an
internal address (cloud metadata 169.254.169.254, docker-network services)
is a data-exfiltration vector, not just a validate-time concern. The store-
time screen in routes/agents/mcps.py is DNS-free and only catches literal
internal hosts; a public-looking name that resolves internal slips past it.
This filter runs the real post-resolution check on the enabled specs right
before a run, so all runtime consumers (websocket, skills) inherit it.

Cloud only (铁律 #7): a local/desktop install is a single trusted user who
legitimately runs MCP servers on localhost; the OS user is the trust
boundary and a local agent has bash anyway.

Note: like the validate-time gate, this NARROWS but does not close the DNS-
rebinding window — the eventual fetch re-resolves the hostname. Full closure
needs connection pinning to the validated IP (tracked as a follow-up).
"""
from __future__ import annotations

from typing import Any, Dict

from loguru import logger

from narranexus.platform.utils.deployment_mode import is_cloud_mode
from narranexus.platform.utils.url_safety import assert_public_http_url


async def filter_public_mcp_servers(
    mcp_servers: Dict[str, Dict[str, Any]],
) -> Dict[str, Dict[str, Any]]:
    """Return only the MCP servers safe to fetch server-side.

    Local mode: returns the input unchanged. Cloud mode: drops any server
    whose URL fails the public-endpoint check. Never logs the URL or the
    resolved IP — only the server name and the exception class.

    Fail-CLOSED: a check that raises ANYTHING (not just UnsafeUrlError — e.g.
    a bare ValueError from urlparse on a malformed URL like ``http://[::1``)
    drops that server. A security filter must never leave a URL in the set
    because deciding on it failed — that would let a malformed row disable the
    gate for the rest of the batch.
    """
    if not mcp_servers or not is_cloud_mode():
        return mcp_servers
    safe: Dict[str, Dict[str, Any]] = {}
    for name, spec in mcp_servers.items():
        url = (spec or {}).get("url") or ""
        try:
            await assert_public_http_url(url)
        except Exception as e:  # noqa: BLE001 — any decision failure MUST fail-closed (drop)
            logger.warning(
                f"Dropping MCP server {name!r}: URL failed the SSRF check ({type(e).__name__})"
            )
            continue
        safe[name] = spec
    return safe
