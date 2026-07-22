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
- Local/desktop: a real HA lives on the LAN (192.168.x / homeassistant.local),
  so private ranges are allowed; we only refuse the link-local metadata range.
- Cloud (agent.narra.nexus): the backend container shares a network with broker
  / mcp / other internal services, so a user-supplied private/loopback host is an
  SSRF vector into the cluster. Cloud users bring a PUBLIC HA (Nabu Casa etc.),
  so cloud rejects any host resolving to a private/loopback/link-local address.
Error strings also never echo the upstream response body (would turn a blind
SSRF into a readable one) — only the status code.
"""

from __future__ import annotations

import ipaddress
import socket
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse

import aiohttp
from loguru import logger

from xyz_agent_context.utils.deployment_mode import is_cloud_mode

_DEFAULT_TIMEOUT = aiohttp.ClientTimeout(total=15, connect=8)
# Cloud metadata service — never a legitimate HA host; refuse to dial it.
_BLOCKED_HOSTS = {"169.254.169.254", "metadata.google.internal"}


class HAError(Exception):
    """A Home Assistant request failed (network, auth, or non-2xx)."""


def validate_base_url(base_url: str) -> str:
    """Return the normalized base URL or raise HAError.

    Enforces http/https, refuses the cloud metadata address, and — in cloud mode
    only — refuses hosts resolving to private/loopback/link-local addresses
    (SSRF guard). Local mode keeps LAN hosts allowed.
    """
    parsed = urlparse(base_url.strip())
    if parsed.scheme not in ("http", "https"):
        raise HAError(f"base_url must be http/https, got: {parsed.scheme or '(none)'}")
    host = parsed.hostname
    if not host:
        raise HAError("base_url has no host")
    if host in _BLOCKED_HOSTS:
        raise HAError(f"host not allowed: {host}")
    cloud = is_cloud_mode()
    try:
        for info in socket.getaddrinfo(host, None):
            ip = ipaddress.ip_address(info[4][0])
            if ip.is_link_local:
                raise HAError(f"host resolves to a link-local address: {ip}")
            if cloud and (ip.is_private or ip.is_loopback or ip.is_reserved):
                raise HAError(
                    "in cloud mode base_url must be a public host "
                    f"(got internal address {ip}); expose your Home Assistant publicly (e.g. Nabu Casa)"
                )
    except socket.gaierror:
        # DNS failure surfaces later as a connection error; don't hard-fail here.
        pass
    return base_url.rstrip("/")


class HAClient:
    """Minimal Home Assistant REST client."""

    def __init__(self, base_url: str, token: str, verify_tls: bool = True):
        self._base = validate_base_url(base_url)
        self._token = token
        self._verify_tls = verify_tls

    @property
    def _headers(self) -> Dict[str, str]:
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    async def _request(self, method: str, path: str, json_body: Optional[dict] = None) -> Any:
        url = f"{self._base}{path}"
        connector = aiohttp.TCPConnector(ssl=self._verify_tls)
        try:
            async with aiohttp.ClientSession(timeout=_DEFAULT_TIMEOUT, connector=connector) as session:
                async with session.request(method, url, headers=self._headers, json=json_body) as resp:
                    if resp.status == 401:
                        raise HAError("unauthorized — check the Home Assistant token")
                    if resp.status >= 400:
                        # Do NOT echo the upstream body — that would turn a blind
                        # SSRF into a readable one. Status code only.
                        raise HAError(f"HA {method} {path} → HTTP {resp.status}")
                    if resp.content_type == "application/json":
                        return await resp.json()
                    return await resp.text()
        except aiohttp.ClientError as e:
            logger.warning(f"HA request failed ({method} {url}): {e}")
            raise HAError(f"could not reach Home Assistant at {self._base}: {e}") from e

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
