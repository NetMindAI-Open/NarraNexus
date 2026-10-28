"""
@file_name: tools.py
@author: Bin Liang
@date: 2026-09-03
@description: The plugin_* MCP tools (spec §11.2) — thin wrappers over ``SelfExtensionService`` returning JSON strings.
"""
from __future__ import annotations

import json
from typing import Any

from loguru import logger
from mcp.server.fastmcp import FastMCP


def _svc(agent_id: str, user_id: str):
    from .service import SelfExtensionService

    return SelfExtensionService(agent_id, user_id)


def _run(fn) -> str:
    try:
        return json.dumps(fn(), default=str)
    except Exception as exc:  # noqa: BLE001 — tools answer with a message, never a traceback
        logger.warning(f"NexusPluginsMCP: {type(exc).__name__}: {exc}")
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


async def _arun(coro) -> str:
    try:
        return json.dumps(await coro, default=str)
    except Exception as exc:  # noqa: BLE001
        logger.warning(f"NexusPluginsMCP: {type(exc).__name__}: {exc}")
        return json.dumps({"error": f"{type(exc).__name__}: {exc}"})


async def _db():
    from narranexus.platform.utils.db.db_factory import get_db_client

    return await get_db_client()


def create_nexus_plugins_mcp_server() -> FastMCP:
    mcp = FastMCP("nexus_plugins_module")

    # ---- awareness: what am I running on, what am I, what can be replaced ----
    # The same cloud refusal as every other tool (SelfExtensionService raises
    # GuardError there); these five do not go through the service, so they
    # guard themselves — the module's "every tool refuses on cloud" promise
    # must hold per tool, not depend on the server never being mounted.
    def _guard() -> None:
        from narranexus.kernel.deployment import is_cloud_mode

        from .guards import GuardError

        if is_cloud_mode():
            raise GuardError("plugin awareness tools are not available on the cloud deployment")

    @mcp.tool()
    async def platform_overview() -> str:
        """The platform you run on: host version, deployment mode, the distribution (if any), every builtin and user plugin, the slot domains and the bindings that differ from the defaults. Start here when you need to reason about the system's shape; go deeper with platform_slots / contract_docs / plugin_docs."""
        from .awareness import platform_overview as _po

        return _run(lambda: (_guard(), _po())[1])

    @mcp.tool()
    async def platform_slots(domain: str = "") -> str:
        """The extension slots of one domain (kernel, prompt, turn, model, agent, ingress, backend, content, ui) — or all — with contract, arity, candidates registered here and what is bound now (and by which layer)."""
        from .awareness import platform_slots as _ps

        return _run(lambda: (_guard(), _ps(domain))[1])

    @mcp.tool()
    async def contract_docs(kind: str) -> str:
        """Detailed documentation of one contract kind (e.g. prompt, framework, tool, module, provider, hook, route): its version and stability, the slots that carry it, the contract classes with docstrings and public methods. Use before writing a plugin for that kind."""
        from .awareness import contract_docs as _cd

        return _run(lambda: (_guard(), _cd(kind))[1])

    @mcp.tool()
    async def agent_self(agent_id: str, user_id: str) -> str:
        """Who you are right now: name/description, whether the caller owns you, your capability switches (enabled, locked, budget), your model slots and framework, and the system-prompt sections in effect."""
        from .awareness import agent_self as _as

        async def go():
            _guard()
            return await _as(await _db(), agent_id, user_id)

        return await _arun(go())

    @mcp.tool()
    async def capability_set(agent_id: str, module_class: str, enabled: bool) -> str:
        """Switch one of YOUR capabilities (a module class from agent_self) on or off; owner only (the caller's identity is taken from the request, not from an argument), base capabilities cannot be switched off; applies from the next turn. Say what you changed and why."""
        from narranexus.platform.module_system import caller_user_id_from_request

        from .awareness import capability_set as _cs

        async def go():
            _guard()
            return await _cs(await _db(), agent_id, caller_user_id_from_request(), module_class, enabled)

        return await _arun(go())

    @mcp.tool()
    async def plugin_list(agent_id: str, user_id: str) -> str:
        """List plugins visible to this agent, drafts in the workspace, pending approvals and the host's contribution points."""
        return _run(lambda: _svc(agent_id, user_id).list())

    @mcp.tool()
    async def plugin_search(agent_id: str, user_id: str, query: str) -> str:
        """Search the official plugin index (metadata only; inclusion is not a code review)."""
        return _run(lambda: _svc(agent_id, user_id).search(query))

    @mcp.tool()
    async def plugin_docs(agent_id: str, user_id: str, kinds: list[str]) -> str:
        """Contract docs and a minimal example for the given kinds (routes, table, worker, hook, settings, tool, mcp_server, bundle, skill, ui_page, ui_panel, theme)."""
        return _run(lambda: _svc(agent_id, user_id).docs(kinds))

    @mcp.tool()
    async def plugin_scaffold(agent_id: str, user_id: str, plugin_id: str, kinds: list[str], display_name: str = "") -> str:
        """Create a draft plugin under this agent's workspace (plugins/<id>) from the kind templates. plugin_id is <publisher>.<name>."""
        return _run(lambda: _svc(agent_id, user_id).scaffold(plugin_id, kinds, display_name))

    @mcp.tool()
    async def plugin_edit(agent_id: str, user_id: str, plugin_id: str, path: str, content: str, why: str = "") -> str:
        """Write one file of the draft (relative path; allow-listed extensions; never credentials). Every edit is logged."""
        return _run(lambda: _svc(agent_id, user_id).edit(plugin_id, path, content, why))

    @mcp.tool()
    async def plugin_validate(agent_id: str, user_id: str, plugin_id: str) -> str:
        """Validate the draft: manifest, contract versions, dependencies, and declared-vs-actual permission scan."""
        return _run(lambda: _svc(agent_id, user_id).validate(plugin_id))

    @mcp.tool()
    async def plugin_test(agent_id: str, user_id: str, plugin_id: str) -> str:
        """Run the draft's tests in a bounded subprocess. Returns a signed report; register requires its report_hash."""
        return _run(lambda: _svc(agent_id, user_id).test(plugin_id))

    @mcp.tool()
    async def plugin_register(agent_id: str, user_id: str, plugin_id: str, report_hash: str) -> str:
        """Register the draft (linked, inactive) for this agent. Needs the green report_hash from plugin_test for the current files."""
        return _run(lambda: _svc(agent_id, user_id).register(plugin_id, report_hash))

    @mcp.tool()
    async def plugin_activate(agent_id: str, user_id: str, plugin_id: str, scope: str = "agent") -> str:
        """Ask the user to activate a registered plugin (scope 'agent' = canary for this agent, 'global' = everyone). Returns a proposal the user decides on."""
        return _run(lambda: _svc(agent_id, user_id).activate(plugin_id, scope))

    @mcp.tool()
    async def plugin_observe(agent_id: str, user_id: str, plugin_id: str, window_hours: int = 24) -> str:
        """Health of an activated plugin over a window: crashes, UI errors, audit events."""
        return _run(lambda: _svc(agent_id, user_id).observe(plugin_id, window_hours * 3600))

    @mcp.tool()
    async def plugin_deactivate(agent_id: str, user_id: str, plugin_id: str) -> str:
        """Disable a plugin (always allowed; takes effect after restart)."""
        return _run(lambda: _svc(agent_id, user_id).deactivate(plugin_id))

    @mcp.tool()
    async def plugin_rollback(agent_id: str, user_id: str, plugin_id: str = "") -> str:
        """Restore the plugin registry from its last-known-good snapshot. Two rollbacks in a row hand control to the user."""
        return _run(lambda: _svc(agent_id, user_id).rollback(plugin_id or None))

    @mcp.tool()
    async def plugin_diff(agent_id: str, user_id: str, plugin_id: str, path: str = "") -> str:
        """Unified diff between the registered version and the current draft."""
        return _run(lambda: _svc(agent_id, user_id).diff(plugin_id, path or None))

    @mcp.tool()
    async def plugin_install(agent_id: str, user_id: str, source: str) -> str:
        """Propose installing a plugin from GitHub (owner/repo[@tag]) or the index; the user confirms with the permissions shown."""
        return _run(lambda: _svc(agent_id, user_id).install(source))

    @mcp.tool()
    async def plugin_upgrade(agent_id: str, user_id: str, plugin_id: str) -> str:
        """Check for a newer release and propose the upgrade (last-known-good is kept)."""
        return _run(lambda: _svc(agent_id, user_id).upgrade(plugin_id))

    @mcp.tool()
    async def plugin_publish_hint(agent_id: str, user_id: str, plugin_id: str) -> str:
        """The steps and checklist to publish the draft as a GitHub Release. Nothing is pushed for you."""
        return _run(lambda: _svc(agent_id, user_id).publish_hint(plugin_id))

    return mcp


__all__ = ["create_nexus_plugins_mcp_server"]
