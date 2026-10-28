"""
@file_name: binding.py
@author: NetMind.AI
@date: 2026-07-14
@description: Resolve an agent's Home Assistant binding into a ready HAClient.

The MCP tools call `resolve_client(agent_id)`; it reads the agent's binding
(keyed on agent_id) via the ChannelCredentialStore seam, parses config_json into
HAConfig, and returns an HAClient (or a human-readable reason the agent should
relay to the user — "not connected yet", "corrupted binding", etc.).

Per-agent binding is the intended model: a user with multiple Home Assistant
instances (home vs. office) can point different agents at different HAs.

DEPLOY CONTRACT: the backend `/verify` route is ALSO a caller of this helper, so
the backend process resolves the binding through the SAME env-gated seam. That is
correct only while `NARRANEXUS_BACKEND_URL` is UNSET in the backend process
(-> DirectStore -> direct db, byte-identical to the old direct read). Never set
that var backend-side — the backend would then HTTP-call ITSELF with no service
bearer, `_require_service_caller` would 403, and HttpStore would degrade to None
so `/verify` falsely reports "not configured". Only the mcp/executor containers
set `NARRANEXUS_BACKEND_URL`.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Tuple

from narranexus.platform.repository import HomeAssistantBindingRepository
from narranexus.platform.schema.home_assistant_schema import HAConfig

from .ha_client import HAClient, HAError

NOT_CONFIGURED = (
    "Home Assistant is not connected yet. Ask the user to bind their Home Assistant "
    "(base URL + Long-Lived Access Token) in the config panel, or run the "
    "`home-assistant-setup` skill to set one up."
)


# ---------------------------------------------------------------------------
# ChannelCredentialStore adapter (blueprint P2, #2). Home Assistant has no
# credential-manager class — it stores a JSON config blob keyed on agent_id.
# This thin adapter gives the seam the uniform ``.get(agent_id) -> obj with
# to_raw_dict()`` shape the other channels' managers have, WITHOUT flattening
# the blob: the raw dict carries ``config_json`` verbatim (which contains the
# Long-Lived Access Token — the secret), so resolve_client keeps parsing it the
# exact same way and preserves the corrupt-vs-not-configured distinction the
# flattened fields would lose.
# ---------------------------------------------------------------------------


@dataclass
class _HABindingCred:
    config_json: str

    def to_raw_dict(self) -> dict[str, Any]:
        return {"config_json": self.config_json}


def _cred_from_raw(raw: dict[str, Any]) -> _HABindingCred:
    return _HABindingCred(config_json=raw.get("config_json", "") or "")


class HomeAssistantCredentialManager:
    """Repository-backed adapter exposing the seam's uniform read shape."""

    def __init__(self, db) -> None:
        self._db = db

    async def get(self, agent_id: str) -> Optional[_HABindingCred]:
        row = await HomeAssistantBindingRepository(self._db).get_by_agent(agent_id)
        if not row or not row.config_json:
            return None
        return _HABindingCred(config_json=row.config_json)


async def resolve_client(agent_id: str) -> Tuple[Optional[HAClient], Optional[str]]:
    """Return (HAClient, None) on success, or (None, reason) to relay to the user.

    Reads the binding via the ChannelCredentialStore seam (DirectStore locally,
    HttpStore -> owner-gated backend endpoint in cloud) so neither the MCP tool
    process nor this helper touches the db directly."""
    from narranexus.platform.module_system.data_access import get_channel_credential_store

    raw = await get_channel_credential_store().get_credential("home_assistant", agent_id)
    if not raw or not raw.get("config_json"):
        return None, NOT_CONFIGURED
    try:
        cfg = HAConfig.model_validate_json(raw["config_json"])
    except Exception:  # noqa: BLE001 — corrupt/legacy config → actionable message
        return None, "The Home Assistant binding is corrupted; please re-bind it in the config panel."
    try:
        return HAClient(cfg.base_url, cfg.token, cfg.verify_tls), None
    except HAError as e:
        return None, str(e)
