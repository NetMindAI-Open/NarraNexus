"""
@file_name: test_module.py
@author: Bin Liang
@date: 2026-09-03
@description: The module is registered (module_registry, core MCP list, port 7811, always-load), is a protected builtin manifest, exposes the sixteen tools, and is silent on cloud.
"""
from __future__ import annotations

import asyncio

import pytest

from narranexus.kernel.plugins.builtins import builtin_manifests
from narranexus.platform.module_system import module_registry
from narranexus.platform.module_system._module_impl.loader import ModuleLoader
from narranexus.platform.module_system.registry import core_module_names
from narranexus_plugins.nexus_plugins_module import NexusPluginsModule
from narranexus.platform.schema.context_schema import ContextData

TOOLS = {
    "plugin_list", "plugin_search", "plugin_docs", "plugin_scaffold", "plugin_edit", "plugin_validate", "plugin_test", "plugin_register",
    "plugin_activate", "plugin_observe", "plugin_deactivate", "plugin_rollback", "plugin_diff", "plugin_install", "plugin_upgrade", "plugin_publish_hint",
    # self-awareness (2026-09-07)
    "platform_overview", "platform_slots", "contract_docs", "agent_self", "capability_set",
}


def test_registration():
    assert module_registry["NexusPluginsModule"] is NexusPluginsModule
    assert "NexusPluginsModule" in core_module_names()
    assert "NexusPluginsModule" in ModuleLoader.always_load_modules(module_registry)
    m = next(m for m in builtin_manifests() if m.id == "builtin.nexus_plugins_module")
    assert m.protected and m.hosts == ("backend", "mcp")


def test_config_tools_and_cloud_silence(env, monkeypatch):
    module = NexusPluginsModule("a1", "u1", None)
    assert module.get_config().module_type == "capability" and module.config.name == "NexusPluginsModule"
    mcp = module.create_mcp_server()
    names = {t.name for t in asyncio.run(mcp.list_tools())}
    assert names == TOOLS
    ctx = ContextData(agent_id="a1", user_id="u1", input_content="hi")
    text = asyncio.run(module.contribute_instructions(ctx))
    assert "plugin_scaffold" in text and "approval" in text
    assert asyncio.run(module.mcp_server()).server_name == "nexus_plugins_module"
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    assert asyncio.run(module.contribute_instructions(ctx)) == ""
    assert asyncio.run(module.mcp_server()) is None


# Every tool's concrete expected shape for THIS fixture's initial (empty)
# plugin state, driven through the real MCP `call_tool` boundary (not the
# service directly, as `tests/nexus_plugins_module/test_flow.py` does) — a
# wrapper-level regression in `_nexus_plugins_impl/tools.py` (wrong service
# method, swallowed return value, mis-forwarded args) would pass a plain
# "json.loads doesn't raise" check but fails these. Success-path tools assert
# the returned key set and NOT `error`; guard/error-path tools assert the
# CONCRETE, stable substring of the message (not the whole message — paths
# like the tmp workspace vary per test run) so a different failure mode
# (e.g. the guard firing for the wrong reason) is caught. The five
# self-awareness tools (platform_overview/platform_slots/contract_docs/
# agent_self/capability_set) have their positive paths covered in depth by
# plugins/builtin.nexus_plugins_module/tests/test_awareness_tools.py; only
# `capability_set`'s error path (unowned agent) is asserted here since that
# is the shape this fixture's empty DB actually produces.
SUCCESS_KEYS = {
    "plugin_list": {"registered", "drafts", "safe_mode", "pending_proposals", "slots"},
    "plugin_docs": {"routes", "_workflow"},
    "plugin_diff": {"plugin_id", "registered", "tree_hash"},
    "plugin_install": {"proposal_id", "status"},
    "plugin_publish_hint": {"plugin_id", "checklist_problems", "steps", "note"},
    "plugin_validate": {"ok", "plugin_id", "problems", "mismatches"},
    "platform_overview": {"host_version", "builtin_plugins", "slot_domains"},
    "platform_slots": None,  # a bare list, not a dict
    "contract_docs": {"kind", "stability", "slots", "contracts"},
    "agent_self": {"agent", "capabilities", "model_slots", "prompt_sections"},
}
ERROR_SUBSTRINGS = {
    "plugin_scaffold": "builtin. prefix is reserved",
    "plugin_edit": "has no draft; scaffold it first",
    "plugin_activate": "is not registered",
    "plugin_observe": "is not registered",
    "plugin_deactivate": "is not registered",
    "plugin_upgrade": "is not registered",
    "plugin_register": "run plugin_test first",
    "plugin_rollback": "no last-known-good snapshot",
    "plugin_test": "No such file or directory",
    "capability_set": "caller identity unavailable",  # the identity is injected, never a tool argument; absent → fail-closed
}


@pytest.mark.parametrize("tool", sorted(TOOLS))
def test_tools_answer_json_never_raise(env, tool, monkeypatch):
    module = NexusPluginsModule("a1", "u1", None)
    mcp = module.create_mcp_server()
    args = {"agent_id": "a1", "user_id": "u1"}
    extra = {
        "plugin_search": {"query": "x"}, "plugin_docs": {"kinds": ["routes"]}, "plugin_scaffold": {"plugin_id": "builtin.x", "kinds": ["routes"]},
        "plugin_edit": {"plugin_id": "me.x", "path": "a.py", "content": ""}, "plugin_validate": {"plugin_id": "me.x"}, "plugin_test": {"plugin_id": "me.x"},
        "plugin_register": {"plugin_id": "me.x", "report_hash": "h"}, "plugin_activate": {"plugin_id": "me.x"}, "plugin_observe": {"plugin_id": "me.x"},
        "plugin_deactivate": {"plugin_id": "me.x"}, "plugin_rollback": {}, "plugin_diff": {"plugin_id": "me.x"}, "plugin_install": {"source": "acme/x"},
        "plugin_upgrade": {"plugin_id": "me.x"}, "plugin_publish_hint": {"plugin_id": "me.x"}, "plugin_list": {},
        "platform_overview": {}, "platform_slots": {"domain": "prompt"}, "contract_docs": {"kind": "prompt"}, "agent_self": {},
        "capability_set": {"module_class": "JobModule", "enabled": False},
    }[tool]
    import json

    monkeypatch.setattr("narranexus_plugins.nexus_plugins_module._nexus_plugins_impl.service.agent_workspace_path", lambda a, u: env["workspace"])
    result = asyncio.run(mcp.call_tool(tool, {**args, **extra}))
    content = result[0] if isinstance(result, tuple) else result  # (content, structured) on newer fastmcp
    first = content[0] if isinstance(content, list) else content.content[0]
    payload = first.text
    parsed = json.loads(payload)

    if tool in ERROR_SUBSTRINGS:
        assert isinstance(parsed, dict) and "error" in parsed, parsed
        assert ERROR_SUBSTRINGS[tool] in parsed["error"], parsed
    elif tool == "plugin_search":
        assert isinstance(parsed, list)  # the official index (unreachable in test) degrades to no results, not an error
    elif SUCCESS_KEYS.get(tool) is None:
        assert isinstance(parsed, list)  # platform_slots: a bare list of domain groups
    else:
        assert isinstance(parsed, dict) and "error" not in parsed, parsed
        assert SUCCESS_KEYS[tool] <= set(parsed.keys()), parsed
        if tool == "plugin_validate":
            assert parsed["ok"] is False and "narranexus-plugin.json is missing" in parsed["problems"]
