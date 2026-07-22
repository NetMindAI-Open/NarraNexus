"""
@file_name: binding.py
@author: NetMind.AI
@date: 2026-07-14
@description: Resolve an agent's Home Assistant binding into a ready HAClient.

The MCP tools call `resolve_client(db, agent_id)`; it reads the agent's binding
row (keyed on agent_id), parses config_json into HAConfig, and returns an
HAClient (or a human-readable reason the agent should relay to the user — "not
connected yet", "corrupted binding", etc.).

Per-agent binding is the intended model: a user with multiple Home Assistant
instances (home vs. office) can point different agents at different HAs.
"""

from __future__ import annotations

from typing import Optional, Tuple

from xyz_agent_context.repository import HomeAssistantBindingRepository
from xyz_agent_context.schema.home_assistant_schema import HAConfig

from .ha_client import HAClient, HAError

NOT_CONFIGURED = (
    "Home Assistant is not connected yet. Ask the user to bind their Home Assistant "
    "(base URL + Long-Lived Access Token) in the config panel, or run the "
    "`home-assistant-setup` skill to set one up."
)


async def resolve_client(db, agent_id: str) -> Tuple[Optional[HAClient], Optional[str]]:
    """Return (HAClient, None) on success, or (None, reason) to relay to the user."""
    row = await HomeAssistantBindingRepository(db).get_by_agent(agent_id)
    if not row or not row.config_json:
        return None, NOT_CONFIGURED
    try:
        cfg = HAConfig.model_validate_json(row.config_json)
    except Exception:  # noqa: BLE001 — corrupt/legacy config → actionable message
        return None, "The Home Assistant binding is corrupted; please re-bind it in the config panel."
    try:
        return HAClient(cfg.base_url, cfg.token, cfg.verify_tls), None
    except HAError as e:
        return None, str(e)
