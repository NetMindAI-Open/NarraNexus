"""
@file_name: wallet_client.py
@author: Bin Liang
@date: 2026-07-28
@description: Client for the deploy-side ``quota-api`` wallet service.

The free tier's money lives on the LiteLLM gateway, and only ``quota-api``
holds that gateway's admin credential. This client is NarraNexus's whole view
of it: open a wallet, read a balance. We never learn what a "virtual key" is —
we receive an opaque api_key exactly like a user pasting their own.

Errors are split so callers can react correctly rather than lumping everything
into "it failed":
  * ``WalletUnavailable``  — transport / 5xx. Transient; the login path
    swallows it and the next login retries.
  * ``WalletDenied``       — 401/403. A misconfigured shared token; retrying
    will not help, and it must be loud in the logs.
  * ``WalletMissing``      — 404 on a balance read. The user simply has no
    wallet (free tier off, or provisioning never ran).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import httpx
from loguru import logger

_DEFAULT_TIMEOUT_S = 10.0


class WalletError(Exception):
    """Base for wallet-service failures."""


class WalletUnavailable(WalletError):
    """The service is unreachable or broken — retry later."""


class WalletDenied(WalletError):
    """The service rejected our credentials — a deployment misconfiguration."""


class WalletMissing(WalletError):
    """No wallet exists for this user."""


@dataclass(frozen=True)
class WalletBalance:
    currency: str
    max_budget: float
    spend: float
    remaining: float
    exhausted: bool

    @classmethod
    def from_payload(cls, data: dict) -> "WalletBalance":
        return cls(
            currency=str(data.get("currency") or "USD"),
            max_budget=float(data.get("max_budget") or 0.0),
            spend=float(data.get("spend") or 0.0),
            remaining=float(data.get("remaining") or 0.0),
            exhausted=bool(data.get("exhausted")),
        )


@dataclass(frozen=True)
class ProvisionedWallet:
    created: bool
    balance: WalletBalance
    # Present ONLY when this call created the wallet — the gateway hands the
    # secret out once. A repeat call returns created=False and no key, which is
    # why the provisioner must treat "already exists" as a no-op rather than
    # trying to re-read the key from anywhere.
    api_key: Optional[str]


class WalletClient:
    """Talks to ``quota-api``. Stateless; cheap to construct per call."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
        transport: Optional[httpx.AsyncBaseTransport] = None,
    ) -> None:
        self._base_url = (base_url or "").rstrip("/")
        self._token = token or ""
        self._timeout_s = timeout_s
        self._transport = transport

    @classmethod
    def from_settings(cls) -> Optional["WalletClient"]:
        """Build from env, or None when the free tier is not configured.

        Returning None (rather than raising) lets every caller express "the free
        tier is simply not part of this deployment" as a plain ``is None``
        check — local mode, desktop, and a cloud deploy with the feature off all
        take the same branch.
        """
        import os

        base = (os.environ.get("FREE_TIER_WALLET_API_URL") or "").strip()
        token = (os.environ.get("FREE_TIER_WALLET_API_TOKEN") or "").strip()
        if not (base and token):
            return None
        return cls(base, token)

    async def provision(self, user_id: str) -> ProvisionedWallet:
        """Open the user's wallet. Idempotent on the service side."""
        data = await self._request("POST", "/v1/wallets", json={"user_id": user_id})
        return ProvisionedWallet(
            created=bool(data.get("created")),
            balance=WalletBalance.from_payload(data.get("wallet") or {}),
            api_key=data.get("api_key"),
        )

    async def balance(self, user_id: str) -> WalletBalance:
        data = await self._request("GET", f"/v1/wallets/{user_id}")
        return WalletBalance.from_payload(data)

    async def served_models(self) -> list[str]:
        """Model ids the gateway actually routes.

        This is the free tier's honest catalogue. The upstream provider sells
        far more models than our gateway is configured with, and a model the
        gateway does not know has no price either — so offering the upstream
        list would put choices in the dropdown that 400 on first use AND could
        not be billed if they didn't.
        """
        data = await self._request("GET", "/v1/models")
        models = data.get("models")
        return [str(m) for m in models] if isinstance(models, list) else []

    async def topup(self, user_id: str, amount_usd: float) -> WalletBalance:
        data = await self._request(
            "POST", f"/v1/wallets/{user_id}/topup", json={"amount_usd": amount_usd}
        )
        return WalletBalance.from_payload(data)

    # -- transport ---------------------------------------------------------

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        try:
            async with httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_s,
                headers={"Authorization": f"Bearer {self._token}"},
                transport=self._transport,
            ) as client:
                resp = await client.request(method, path, **kwargs)
        except Exception as e:  # noqa: BLE001 — transport-level
            raise WalletUnavailable(f"wallet service unreachable: {e!r}") from e

        if resp.status_code == 404:
            raise WalletMissing(f"no wallet ({method} {path})")
        if resp.status_code in (401, 403):
            logger.error(
                "[wallet] quota-api rejected our token — check "
                "FREE_TIER_WALLET_API_TOKEN vs QUOTA_API_TOKEN"
            )
            raise WalletDenied(f"wallet service denied us ({resp.status_code})")
        if resp.status_code >= 400:
            raise WalletUnavailable(
                f"wallet service {method} {path} -> {resp.status_code}"
            )
        try:
            return resp.json()
        except Exception as e:  # noqa: BLE001
            raise WalletUnavailable(f"wallet service returned non-JSON: {e!r}") from e
