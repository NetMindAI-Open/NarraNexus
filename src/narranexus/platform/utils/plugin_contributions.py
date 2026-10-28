"""
@file_name: plugin_contributions.py
@author: Bin Liang
@date: 2026-09-03
@description: Read-side helpers over the kernel registries for the agent-facing contribution kinds.

The consumers (context runtime for MCP servers and tools, the skill module
for skills, the team marketplace for bundles) all need the same three
things: iterate a registry in declaration order, build each entry through
its factory, and isolate a broken entry (log + skip) instead of failing the
turn. This module is that loop, typed per kind, so no consumer re-implements
it and a plugin that raises in its factory costs one warning, never a turn.
"""
from __future__ import annotations

from typing import Any, Callable, Iterator, TypeVar

from loguru import logger

from narranexus.contracts.bundle import BundleSpec
from narranexus.contracts.mcp_server import McpServerSpec
from narranexus.contracts.skill import SkillSpec
from narranexus.contracts.tool import ToolProvider, ToolSpec

T = TypeVar("T")

MCP_SERVERS_SLOT = "agent.capabilities.mcp_servers"
TOOLS_SLOT = "agent.capabilities.tools"
SKILLS_SLOT = "content.skills"
BUNDLES_SLOT = "content.bundles"


def _registries(registries: Any = None) -> Any:
    if registries is not None:
        return registries
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

    return KERNEL_REGISTRIES


def _built(slot: str, expected: type, registries: Any, *, check: Callable[[Any], bool] | None = None) -> Iterator[tuple[str, str, Any]]:
    """Yield ``(owner, name, value)`` for every entry of ``slot`` whose factory builds an ``expected``."""
    for entry in _registries(registries).registry_for(slot).entries():
        try:
            value = entry.factory()
        except Exception as exc:  # noqa: BLE001 — isolate the plugin, keep the turn
            logger.warning(f"[plugins] {entry.owner}: {slot} entry {entry.name!r} failed to build: {exc}")
            continue
        if not isinstance(value, expected) or (check is not None and not check(value)):
            logger.warning(
                f"[plugins] {entry.owner}: {slot} entry {entry.name!r} is not a {expected.__name__}; ignored"
            )
            continue
        yield entry.owner, entry.name, value


def plugin_mcp_servers(registries: Any = None) -> dict[str, dict[str, Any]]:
    """Site-level MCP servers as ``{name: config}`` in the shape the turn's ``mcp_servers`` uses.

    URL transports become ``{"url", "headers"}`` (the module servers' shape);
    stdio servers carry ``command/args/env``, where ``env`` also holds what a
    child interpreter needs to import ``nxplugins.<id>`` (see
    ``kernel.plugins.subprocess_env``) — without it every template stdio
    server died with "No module named nxplugins". Names are the spec's own; a
    later duplicate name loses (first declared wins) so the surface stays
    deterministic.
    """
    from narranexus.kernel.plugins.subprocess_env import subprocess_env

    out: dict[str, dict[str, Any]] = {}
    for owner, _, spec in _built(MCP_SERVERS_SLOT, McpServerSpec, registries):
        if spec.name in out:
            logger.warning(f"[plugins] {owner}: mcp server {spec.name!r} already provided; ignored")
            continue
        if spec.transport == "stdio":
            out[spec.name] = {"command": spec.command, "args": list(spec.args), "env": subprocess_env(spec.env)}
        else:
            cfg: dict[str, Any] = {"url": spec.url}
            if spec.headers:
                cfg["headers"] = dict(spec.headers)
            out[spec.name] = cfg
    return out


def plugin_tools(registries: Any = None) -> tuple[ToolSpec, ...]:
    """Every plugin tool in declaration order (providers in registry order, tools in provider order)."""
    out: list[ToolSpec] = []
    seen: set[str] = set()
    for owner, name, provider in _built(TOOLS_SLOT, object, registries, check=lambda v: isinstance(v, ToolProvider)):
        try:
            tools = tuple(provider.list_tools())
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[plugins] {owner}: tool provider {name!r} list_tools failed: {exc}")
            continue
        for tool in tools:
            if not isinstance(tool, ToolSpec) or tool.name in seen:
                continue
            seen.add(tool.name)
            out.append(tool)
    return tuple(out)


def plugin_skills(registries: Any = None) -> tuple[tuple[str, SkillSpec], ...]:
    """``(owner, SkillSpec)`` for every skill directory that actually has a SKILL.md."""
    return tuple(
        (owner, spec)
        for owner, _, spec in _built(SKILLS_SLOT, SkillSpec, registries)
        if spec.manifest_path.is_file()
    )


def plugin_bundles(registries: Any = None) -> tuple[tuple[str, BundleSpec], ...]:
    """``(owner, BundleSpec)`` for every bundle whose file exists."""
    return tuple((owner, spec) for owner, _, spec in _built(BUNDLES_SLOT, BundleSpec, registries) if spec.path.is_file())


__all__ = [
    "BUNDLES_SLOT",
    "MCP_SERVERS_SLOT",
    "SKILLS_SLOT",
    "TOOLS_SLOT",
    "plugin_bundles",
    "plugin_mcp_servers",
    "plugin_skills",
    "plugin_tools",
]
