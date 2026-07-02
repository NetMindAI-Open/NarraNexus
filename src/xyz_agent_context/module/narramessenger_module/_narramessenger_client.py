"""
@file_name: _narramessenger_client.py
@date: 2026-06-17
@description: Async HTTP client for the NarraMessenger backend (raw aiohttp).

NarraMessenger ("Narra hybrid-im-backend", formerly NexusMatrix) is a
Matrix-based IM platform that exposes a plain HTTPS-JSON contract for
external agent runtimes. We integrate via the **Gateway Polling** transport
plus the chat-proxy ``/chat/send`` endpoint — pure bearer-token HTTP, no
Matrix client. See the design doc
``Work/Narramessenger 接入/2026-06-17 NarraMessenger 接入设计.md``.

Endpoints (all ``Authorization: Bearer <bearer_token>``):
  - POST {base}/api/agent-gateway/connect                       — activate transport
  - GET  {base}/api/agent-gateway/invocations/poll?timeout=ms   — long-poll inbound
  - POST {base}/api/agent-gateway/update-guide/ack              — ack runtime-contract update
  - POST {base}/api/agent-runtime/chat/send                     — outbound (reply / proactive)
  - GET  {base}/api/agent-runtime/status                        — liveness

Mirrors ``telegram_sdk_client.py`` shape: thin per-method wrappers plus a
shared aiohttp session. The bearer token lives ONLY in the Authorization
header — never in a URL path — so URLs are safe to log.
"""

from __future__ import annotations

from typing import Any, Optional

import aiohttp
from loguru import logger


class NarramessengerAPIError(RuntimeError):
    """Raised when the NarraMessenger backend returns an HTTP/contract error.

    Carries the HTTP ``status`` and a short ``code`` so callers can branch
    (e.g. permanent 401/409 vs transient 5xx) without parsing strings.
    """

    def __init__(self, code: str, status: int = 0, message: str = ""):
        super().__init__(message or code)
        self.code = code
        self.status = status


class NarramessengerClient:
    """Async NarraMessenger backend client. One instance per credential / call site."""

    # Long-poll blocks up to 30s server-side; client timeout must exceed it.
    _POLL_TOTAL_TIMEOUT = 40.0
    _SHORT_TIMEOUT = 20.0

    def __init__(self, bearer_token: str, backend_base_url: str):
        # bearer_token may be "" for the public bind-flow calls
        # (``fetch_setup_guide`` / ``report_profile`` use the path/query bind
        # token, not the Authorization header). All runtime endpoints require
        # a real bearer and will get 401 from the server if it is missing.
        if not backend_base_url:
            raise ValueError("backend_base_url must be provided")
        self._bearer = bearer_token
        # Normalise: no trailing slash so f"{base}/api/..." is clean.
        self._base = backend_base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    @property
    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._bearer}",
            "Content-Type": "application/json",
        }

    async def _ensure_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            # trust_env=True so standard HTTP(S)_PROXY / NO_PROXY env vars are
            # honoured — matches telegram_sdk_client (CN dev proxy support).
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self._POLL_TOTAL_TIMEOUT),
                trust_env=True,
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def __aenter__(self) -> "NarramessengerClient":
        await self._ensure_session()
        return self

    async def __aexit__(self, *_exc) -> None:
        await self.close()

    # ------------------------------------------------------------------
    # Low-level request helper
    # ------------------------------------------------------------------

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json_body: Optional[dict] = None,
        total_timeout: Optional[float] = None,
    ) -> dict[str, Any]:
        """Issue one request. Raises NarramessengerAPIError on non-2xx.

        Returns the parsed JSON dict (or ``{}`` for an empty body).
        """
        url = f"{self._base}{path}"
        session = await self._ensure_session()
        timeout = (
            aiohttp.ClientTimeout(total=total_timeout)
            if total_timeout is not None
            else None
        )
        try:
            async with session.request(
                method, url, json=json_body, headers=self._headers, timeout=timeout
            ) as resp:
                # The backend returns JSON for both success and most errors.
                try:
                    data = await resp.json()
                except Exception:  # noqa: BLE001 — non-JSON error body
                    data = {}
                if resp.status < 200 or resp.status >= 300:
                    code = ""
                    if isinstance(data, dict):
                        code = str(data.get("error") or data.get("code") or "")
                    raise NarramessengerAPIError(
                        code or f"http_{resp.status}",
                        status=resp.status,
                        message=f"{method} {path} -> {resp.status}",
                    )
                return data if isinstance(data, dict) else {"result": data}
        except aiohttp.ClientError as e:
            raise NarramessengerAPIError(
                f"client_error:{type(e).__name__}", status=0, message=str(e)
            ) from e

    # ------------------------------------------------------------------
    # Gateway transport
    # ------------------------------------------------------------------

    async def connect(self) -> dict[str, Any]:
        """Activate the Gateway Polling transport. Call once on startup.

        Returns ``{status, firstConnect, agentId, principalId, matrixUserId,
        roomId?}``. HTTP 409 means platform-side credentials are missing or
        revoked (permanent — re-bind); 5xx is transient (retry with backoff).
        """
        return await self._request(
            "POST", "/api/agent-gateway/connect", total_timeout=self._SHORT_TIMEOUT
        )

    async def poll(self, timeout_ms: int = 30000) -> dict[str, Any]:
        """Long-poll for one invocation.

        Returns an invocation payload, or ``{"status": "no_invocation"}``,
        or ``{"status": "update_guide_required", ...}``. Blocks up to
        ``timeout_ms`` server-side.
        """
        return await self._request(
            "GET",
            f"/api/agent-gateway/invocations/poll?timeout={int(timeout_ms)}",
            total_timeout=self._POLL_TOTAL_TIMEOUT,
        )

    async def ack_update_guide(self, version: int) -> dict[str, Any]:
        """Acknowledge a Gateway update-guide control payload.

        We pin the integration's contract version and ack programmatically —
        we do NOT execute the runtime self-update ``update_document``.
        """
        return await self._request(
            "POST",
            "/api/agent-gateway/update-guide/ack",
            json_body={"version": int(version)},
            total_timeout=self._SHORT_TIMEOUT,
        )

    # ------------------------------------------------------------------
    # Outbound (reply / proactive send) — chat proxy
    # ------------------------------------------------------------------

    async def chat_send(
        self,
        room_id: str,
        text: str,
        txn_id: str,
        conversation_type: Optional[str] = None,
    ) -> dict[str, Any]:
        """Send a text message to an authorized room.

        Bearer-only, no invocation binding, no reply deadline — this is the
        v1 reply AND proactive-send path. Returns ``data`` with ``event_id``.
        """
        body: dict[str, Any] = {"room_id": room_id, "text": text, "txn_id": txn_id}
        if conversation_type:
            body["conversation_type"] = conversation_type
        resp = await self._request(
            "POST",
            "/api/agent-runtime/chat/send",
            json_body=body,
            total_timeout=self._SHORT_TIMEOUT,
        )
        # Envelope shape: {"command": "im send", "status": "ok", "data": {...}}
        return resp.get("data", resp) if isinstance(resp, dict) else {}

    async def reply(self, invocation_id: str, text: str) -> dict[str, Any]:
        """Reply to a specific gateway invocation.

        This BOTH delivers the message AND closes the invocation — so it does
        not leave the invocation hanging until the 15-min server deadline
        (unlike ``chat_send``, which delivers but never acks). Use this for
        replying to the message the agent was invoked on; use ``chat_send``
        for proactive/agent-initiated messages (no invocation to ack).

        Returns ``{"success": true}`` on success.
        """
        return await self._request(
            "POST",
            f"/api/agent-gateway/invocations/{invocation_id}/reply",
            json_body={"text": text},
            total_timeout=self._SHORT_TIMEOUT,
        )

    async def status(self) -> dict[str, Any]:
        """Liveness / binding status for this bearer token."""
        return await self._request(
            "GET", "/api/agent-runtime/status", total_timeout=self._SHORT_TIMEOUT
        )

    # ------------------------------------------------------------------
    # Bind flow (public bind token) — used by _narramessenger_service.do_bind
    # ------------------------------------------------------------------

    async def fetch_setup_guide(self, bind_token: str) -> str:
        """GET the public setup-guide markdown for a bind token.

        Returns the raw markdown text. Raises NarramessengerAPIError on a
        non-2xx (invalid/expired token). No bearer needed — the path token is
        the credential.
        """
        url = f"{self._base}/{bind_token}/setup-guide.md"
        session = await self._ensure_session()
        try:
            async with session.get(
                url, timeout=aiohttp.ClientTimeout(total=self._SHORT_TIMEOUT)
            ) as resp:
                body = await resp.text()
                if resp.status < 200 or resp.status >= 300:
                    raise NarramessengerAPIError(
                        f"setup_guide_http_{resp.status}",
                        status=resp.status,
                        message=f"GET /{bind_token}/setup-guide.md -> {resp.status}",
                    )
                return body
        except aiohttp.ClientError as e:
            raise NarramessengerAPIError(
                f"client_error:{type(e).__name__}", status=0, message=str(e)
            ) from e

    async def report_profile(self, bind_token: str, name: str, bio: str) -> dict[str, Any]:
        """POST the agent profile to advance the bind session (public token)."""
        url = f"{self._base}/bind-agent/report-profile?token={bind_token}"
        session = await self._ensure_session()
        try:
            async with session.post(
                url,
                json={"name": name, "bio": bio},
                headers={"Content-Type": "application/json"},
                timeout=aiohttp.ClientTimeout(total=self._SHORT_TIMEOUT),
            ) as resp:
                try:
                    data = await resp.json()
                except Exception:  # noqa: BLE001
                    data = {}
                if resp.status < 200 or resp.status >= 300:
                    code = ""
                    if isinstance(data, dict):
                        code = str(data.get("error") or "")
                    raise NarramessengerAPIError(
                        code or f"report_profile_http_{resp.status}",
                        status=resp.status,
                        message=f"report-profile -> {resp.status}",
                    )
                return data if isinstance(data, dict) else {}
        except aiohttp.ClientError as e:
            raise NarramessengerAPIError(
                f"client_error:{type(e).__name__}", status=0, message=str(e)
            ) from e


def is_permanent_api_error(exc: BaseException) -> bool:
    """True for errors that will not recover by retrying (revoked / unauthorized).

    HTTP 401 (token invalid) and 409 (platform Matrix credentials missing or
    revoked) are terminal: the watcher must stop reconnecting and the bind
    has to be redone. Everything else (5xx, network) is transient.
    """
    if isinstance(exc, NarramessengerAPIError):
        return exc.status in (401, 409)
    return False
