"""
@file_name: test_hello_module.py
@author: Bin Liang
@date: 2026-09-04
@description: The plugin's own test: booted through the SDK test host, its module and table register, its declaration carries what the platform needs, and its tool records a note.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_module_registers_and_its_tool_records_a_note(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home", role="backend") as host:
        assert host.names("agent.capabilities.modules") == ("AcmeNotesModule",)
        assert host.names("backend.tables") == ("notes",)
        from nxplugins.acme_hello_module import NOTES, AcmeNotesModule

        cfg = AcmeNotesModule.get_config()
        assert cfg.decision is not None and cfg.agent_instance is not None and cfg.always_load
        module = AcmeNotesModule("agent_t", "u1", None)
        server = module.create_mcp_server()
        assert server is not None and [t.name for t in asyncio.run(server.list_tools())] == ["note_add"]
        assert "note_add" in asyncio.run(module.contribute_instructions(None))
        NOTES.clear()
