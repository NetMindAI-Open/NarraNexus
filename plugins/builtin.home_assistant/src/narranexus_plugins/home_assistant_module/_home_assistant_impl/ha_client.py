"""
@file_name: ha_client.py
@author: NetMind.AI
@date: 2026-07-14
@description: Thin async REST client for a user's Home Assistant instance.

Talks ONLY to HA's stable REST API (Apache-2.0 HA Core) with a Long-Lived
Access Token — brand-agnostic (Xiaomi/Aqara/Hue/… all appear as HA entities).
Deployment-agnostic: `base_url` is a LAN HA (local/desktop) or an exposed HA
(cloud, e.g. Nabu Casa). Each call opens a short-lived aiohttp session with a
bounded timeout.

SSRF note: the user supplies `base_url`, so the backend makes an outbound
request to a user-controlled host. Validation is DEPLOYMENT-AWARE:
- Cloud (agent.narra.nexus): the backend container shares a network with the
  metadata endpoint, broker / mcp / llm-gateway and every other internal
  service. Every request first passes the shared deployment-aware egress
  gate (`url_safety.assert_public_http_url_in_cloud`): the host must resolve,
  and only to public addresses — a DNS failure is a reject, not a pass. Cloud users bring a
  PUBLIC HA (Nabu Casa etc.).
- Local/desktop: a real HA lives on the LAN (192.168.x / homeassistant.local),
  so private ranges stay allowed; the host is still resolved and any
  link-local answer (the metadata range — reachable via spellings like
  `2852039166`, `169.254.169.254.` or `instance-data`) is refused, as is a
  host that does not resolve.
Redirects are never followed (a public host could 302 into the cluster), and
error strings never echo the upstream body or the network exception text —
status code / generic wording only, details go to the log.
"""

from __future__ import annotations

import asyncio
import ipaddress
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp
from loguru import logger

from narranexus.platform.utils import deployment_mode
from narranexus.platform.utils.url_safety import assert_public_http_url_in_cloud

_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)
# Cloud metadata service — never a legitimate HA host; refuse to dial it.
_BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal"}

_CLOUD_PUBLIC_ONLY = (
    "Home Assistant URL is not allowed: in the cloud it must be a public host "
    "(expose your Home Assistant publicly, e.g. Nabu Casa)"
)


class HAError(Exception):
    """A Home Assistant request failed (network, auth, or non-2xx)."""


def _check_url_shape(base_url: str) -> str:
    """Synchronous, DNS-free checks; return the normalized base URL or raise.

    http/https only, a host present, never the metadata host or a literal
    link-local address. This runs at construction so an obviously bad URL is
    refused before any client exists; the network-aware gate is
    `validate_base_url`.
    """
    parsed = urlparse(base_url.strip())
    if parsed.scheme not in ("http", "https"):
        raise HAError(f"base_url must be http/https, got: {parsed.scheme or '(none)'}")
    host = (parsed.hostname or "").rstrip(".")
    if not host:
        raise HAError("base_url has no host")
    if host in _BLOCKED_HOSTS:
        raise HAError("host not allowed")
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None and literal.is_link_local:
        raise HAError("host not allowed")
    return base_url.strip().rstrip("/")


async def _assert_not_link_local(base_url: str) -> None:
    """Local-mode check: resolve the host, refuse any link-local answer.

    A different policy from the cloud gate (private LAN ranges are the point
    of a local HA), so it is not `assert_public_http_url`. Catches metadata
    spellings a literal check misses (decimal IP, trailing dot, the EC2
    `instance-data` name). A host that does not resolve is refused too.
    """
    parsed = urlparse(base_url)
    host = parsed.hostname or ""
    try:
        infos = await asyncio.get_running_loop().getaddrinfo(host, parsed.port or None)
        addresses = [ipaddress.ip_address(str(info[4][0]).split("%", 1)[0]) for info in infos]
    except Exception as e:  # noqa: BLE001 — unresolvable / unparseable fails closed
        raise HAError("could not resolve the Home Assistant host") from e
    if not addresses or any(ip.is_link_local for ip in addresses):
        raise HAError("host not allowed")


async def validate_base_url(base_url: str) -> str:
    """Return the normalized base URL or raise HAError.

    Shape checks always. Cloud: the shared egress gate (post-resolution,
    fail-closed). Local: LAN hosts allowed, link-local answers refused.
    """
    normalized = _check_url_shape(base_url)
    # Module attribute, not a bound copy: this branch and the shared wrapper
    # must read the SAME symbol, or a patched/forked mode could send HA into
    # the cloud branch while the wrapper decides "local" and checks nothing.
    if deployment_mode.is_cloud_mode():
        try:
            await assert_public_http_url_in_cloud(normalized)
        except Exception as e:  # noqa: BLE001 — any decision failure fails closed
            logger.warning(f"HA base_url refused by egress gate ({type(e).__name__}): {e}")
            raise HAError(_CLOUD_PUBLIC_ONLY) from e
    else:
        await _assert_not_link_local(normalized)
    return normalized


class HAClient:
    """Minimal Home Assistant REST client."""

    def __init__(self, base_url: str, token: str, verify_tls: bool = True):
        self._base = _check_url_shape(base_url)
        self._token = token
        self._verify_tls = verify_tls

    @property
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, json_body: Optional[dict] = None) -> Any:
        # Gate EVERY request, not just construction: a client outlives one DNS
        # answer, and re-checking narrows (does not close) the rebinding window.
        await validate_base_url(self._base)
        url = f"{self._base}{path}"
        connector = aiohttp.TCPConnector(ssl=self._verify_tls)
        try:
            async with aiohttp.ClientSession(timeout=_DEFAULT_TIMEOUT, connector=connector) as session:
                async with session.request(
                    method, url, headers=self._headers, json=json_body, allow_redirects=False
                ) as resp:
                    if resp.status == 401:
                        raise HAError("unauthorized — check the Home Assistant token")
                    if 300 <= resp.status < 400:
                        raise HAError(
                            f"Home Assistant answered with a redirect (HTTP {resp.status}); "
                            "use the final URL of your instance"
                        )
                    if resp.status >= 400:
                        # Do NOT echo the upstream body — that would turn a blind
                        # SSRF into a readable one. Status code only.
                        raise HAError(f"HA {method} {path} → HTTP {resp.status}")
                    if resp.content_type == "application/json":
                        return await resp.json()
                    return await resp.text()
        except aiohttp.ClientError as e:
            logger.warning(f"HA request failed ({method} {url}): {type(e).__name__}: {e}")
            raise HAError("could not reach Home Assistant") from e

    async def ping(self) -> bool:
        """True if the API is reachable and the token authenticates."""
        await self._request("GET", "/api/")
        return True

    async def list_states(self) -> List[Dict[str, Any]]:
        """All entities + current state (GET /api/states)."""
        return await self._request("GET", "/api/states")

    async def get_state(self, entity_id: str) -> Dict[str, Any]:
        """One entity's full state (GET /api/states/{entity_id})."""
        return await self._request("GET", f"/api/states/{entity_id}")

    async def list_services(self) -> List[Dict[str, Any]]:
        """Available services per domain (GET /api/services)."""
        return await self._request("GET", "/api/services")

    async def call_service(
        self, domain: str, service: str, entity_id: Optional[str] = None, data: Optional[dict] = None
    ) -> Any:
        """Invoke a service, e.g. light.turn_on (POST /api/services/{domain}/{service})."""
        payload: Dict[str, Any] = dict(data or {})
        if entity_id:
            payload["entity_id"] = entity_id
        return await self._request("POST", f"/api/services/{domain}/{service}", json_body=payload)
