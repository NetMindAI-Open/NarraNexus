"""
@file_name: api_client.py
@author: NarraNexus E2E
@date: 2026-05-13
@description: Async REST wrapper for the local backend (8000)

Surface intentionally narrow: only the endpoints case authors need.
Higher-level helpers live in fixtures.py. Anything WebSocket-shaped
lives in ws_client.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

import httpx


@dataclass
class LocalUser:
    user_id: str
    display_name: str


@dataclass
class LocalAgent:
    agent_id: str
    name: str
    owner_user_id: str


class APIClient:
    """One client per case. Do not share across cases — concurrent
    cases that share a client would share connection pool state and
    headers, breaking isolation."""

    def __init__(self, base_url: str, http_timeout: float) -> None:
        self.base_url = base_url.rstrip("/")
        self._http = httpx.AsyncClient(base_url=self.base_url, timeout=http_timeout)

    async def close(self) -> None:
        await self._http.aclose()

    async def __aenter__(self) -> "APIClient":
        return self

    async def __aexit__(self, *_exc: Any) -> None:
        await self.close()

    # ---- low-level helpers ----

    async def _post(
        self,
        path: str,
        json: dict,
        *,
        params: Optional[dict] = None,
    ) -> dict:
        resp = await self._http.post(path, json=json, params=params or {})
        body = _safe_json(resp)
        if resp.status_code >= 400:
            raise APIError(path, resp.status_code, body)
        return body

    async def _get(self, path: str, *, params: Optional[dict] = None) -> dict:
        resp = await self._http.get(path, params=params or {})
        body = _safe_json(resp)
        if resp.status_code >= 400:
            raise APIError(path, resp.status_code, body)
        return body

    async def _delete(self, path: str, *, params: Optional[dict] = None) -> dict:
        resp = await self._http.delete(path, params=params or {})
        body = _safe_json(resp)
        if resp.status_code >= 400:
            raise APIError(path, resp.status_code, body)
        return body

    # ---- preflight ----

    async def health(self) -> bool:
        try:
            resp = await self._http.get("/health")
            return resp.status_code == 200
        except httpx.HTTPError:
            return False

    async def list_providers(self, user_id: str) -> list[dict]:
        body = await self._get("/api/providers", params={"user_id": user_id})
        return body.get("providers", []) if isinstance(body, dict) else []

    # ---- users (local mode) ----

    async def create_user(self, user_id: str, display_name: Optional[str] = None) -> LocalUser:
        body = await self._post(
            "/api/auth/create-user",
            {"user_id": user_id, "display_name": display_name or user_id},
        )
        if not body.get("success"):
            raise APILogicError("create-user", body)
        return LocalUser(user_id=body["user_id"], display_name=display_name or user_id)

    # ---- agents ----

    async def create_agent(
        self,
        user_id: str,
        name: str,
        description: Optional[str] = None,
    ) -> LocalAgent:
        body = await self._post(
            "/api/auth/agents",
            {
                "created_by": user_id,
                "agent_name": name,
                "agent_description": description,
            },
        )
        if not body.get("success"):
            raise APILogicError("create-agent", body)
        agent = body["agent"]
        return LocalAgent(
            agent_id=agent["agent_id"],
            name=agent.get("name", name),
            owner_user_id=agent.get("created_by", user_id),
        )

    async def delete_agent(self, agent_id: str, user_id: str) -> dict:
        return await self._delete(
            f"/api/auth/agents/{agent_id}",
            params={"user_id": user_id},
        )

    # ---- providers ----
    # The local backend stores LLM provider config per user_id. A NetMind
    # one-key card auto-creates two providers (anthropic + openai protocol)
    # from the same key; we exploit that to fill both the AGENT and
    # HELPER_LLM slots in one call via ``default_slots``.

    async def add_netmind_card(
        self,
        user_id: str,
        api_key: str,
        *,
        agent_model: str,
        helper_model: str,
        embedding_model: str,
    ) -> dict:
        return await self._post(
            "/api/providers",
            {
                "card_type": "netmind",
                "api_key": api_key,
                "default_slots": {
                    # AGENT slot wants the Anthropic protocol provider
                    # (ClaudeConfig downstream); HELPER_LLM + EMBEDDING
                    # both want the OpenAI protocol provider (OpenAIConfig
                    # downstream). All three slots are required — the
                    # backend rejects agent_runtime startup when any one
                    # is missing.
                    "agent": {"protocol": "anthropic", "model": agent_model},
                    "helper_llm": {"protocol": "openai", "model": helper_model},
                    "embedding": {"protocol": "openai", "model": embedding_model},
                },
            },
            params={"user_id": user_id},
        )


# ---- errors ----


class APIError(RuntimeError):
    """Raised when the backend returns an HTTP error status."""
    def __init__(self, path: str, status: int, body: Any) -> None:
        super().__init__(f"HTTP {status} on {path}: {body!r}")
        self.path = path
        self.status = status
        self.body = body


class APILogicError(RuntimeError):
    """Raised when the backend returned 200 but success=False."""
    def __init__(self, operation: str, body: dict) -> None:
        super().__init__(f"{operation} returned success=False: {body!r}")
        self.operation = operation
        self.body = body


def _safe_json(resp: httpx.Response) -> Any:
    try:
        return resp.json()
    except Exception:
        return {"raw_text": resp.text, "status_code": resp.status_code}
