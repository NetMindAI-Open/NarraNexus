"""
@file_name: home_assistant_schema.py
@author: NetMind.AI
@date: 2026-07-14
@description: Pydantic models for the Home Assistant integration.

A binding stores how to reach ONE user's Home Assistant instance: its base URL
and a Long-Lived Access Token. The token is a sensitive credential — it is
redacted on export (bundle) and masked in the frontend. Deployment-agnostic:
`base_url` points at a LAN HA (local/desktop) or an exposed HA (cloud, e.g.
Nabu Casa / reverse proxy) — the module code only ever needs url + token.
"""

from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field


class HAConfig(BaseModel):
    """Connection config for a Home Assistant instance (stored as config_json)."""

    base_url: str = Field(..., description="HA base URL, e.g. http://homeassistant.local:8123")
    token: str = Field(..., description="Long-Lived Access Token (sensitive)")
    verify_tls: bool = Field(True, description="Verify TLS cert on https base_url")
    note: Optional[str] = None


class HABinding(BaseModel):
    """A per-agent Home Assistant binding record."""

    agent_id: str
    config: HAConfig
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
