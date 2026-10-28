"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-04
@description: Hello Module — a non-builtin L4 module installed as a plugin (batch 5 exit criterion).
"""
from __future__ import annotations

from typing import Any, Optional

from narranexus.contracts.table import ColumnSpec, TableSpec
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.module_system import mcp_server_url
from narranexus.platform.module_system.base import XYZBaseModule
from narranexus.platform.schema.module_schema import (
    MCPServerConfig,
    ModuleAgentInstance,
    ModuleConfig,
    ModuleDecisionMeta,
    ModuleDisplay,
)

NOTES: list[dict[str, str]] = []


class AcmeNotesModule(XYZBaseModule):
    """Keeps short notes for the user and lets the agent add one through MCP."""

    @staticmethod
    def get_config() -> ModuleConfig:
        return ModuleConfig(
            name="AcmeNotesModule",
            priority=42,
            enabled=True,
            description="Keeps the user's short notes",
            always_load=True,
            instance_prefix="notes",
            context_cost_hint=150,
            display=ModuleDisplay(icon="📝", name="Notes", desc="Personal notes"),
            decision=ModuleDecisionMeta(capabilities=["Store a short note"], use_cases=["Remember something for later"], instance_type="persistent"),
            agent_instance=ModuleAgentInstance(description="Personal notes", keywords=["note", "remember"], topic_hint="Notes the user asked to keep"),
        )

    async def contribute_instructions(self, ctx_data: Any) -> str:
        return "## Notes\nWhen the user asks you to remember something, call `note_add(text)`."

    async def contribute_turn_context(self, ctx_data: Any) -> str:
        return f"Notes kept: {len(NOTES)}" if NOTES else ""

    async def mcp_server(self) -> Optional[MCPServerConfig]:
        return MCPServerConfig(server_name="hello_module", server_url=mcp_server_url("hello_module"), type="sse")

    def create_mcp_server(self) -> Optional[Any]:
        from mcp.server.fastmcp import FastMCP

        mcp = FastMCP("hello_module")
        module = self

        @mcp.tool(name="note_add")
        async def note_add(text: str) -> dict:
            NOTES.append({"agent_id": module.agent_id, "text": text})
            return {"ok": True, "count": len(NOTES)}

        return mcp


MODULES = (Contribution("AcmeNotesModule", lambda: AcmeNotesModule, meta={"plugin_id": "acme.hello_module", "channel": False}),)
TABLES = (
    Contribution(
        "notes",
        lambda: TableSpec(
            "ext_acme_hello_module__notes",
            (
                ColumnSpec("id", "INTEGER", "BIGINT UNSIGNED", nullable=False, primary_key=True, auto_increment=True),
                ColumnSpec("agent_id", "TEXT", "VARCHAR(64)", nullable=False),
                ColumnSpec("text", "TEXT", "VARCHAR(1024)", nullable=False),
            ),
        ),
    ),
)

__all__ = ["MODULES", "NOTES", "TABLES", "AcmeNotesModule"]
