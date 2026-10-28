"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2025-12-22
@description: Unified exports for the module system (the platform-side half of "a module is a plugin capability")

Module structure:
    module_system/
    ├── __init__.py           # This file - unified exports
    ├── base.py               # XYZBaseModule base class (a Capability)
    ├── module_service.py     # Module service (protocol layer)
    ├── hook_manager.py       # Hook manager
    ├── module_runner.py      # MCP host: one port, every module mounted by path
    ├── registry.py           # ModuleRegistry — a view over the agent.capabilities.modules slot
    ├── contributions.py      # Builtin module/trigger/channel specs (being moved into the plugins)
    ├── _mcp_identity.py      # Caller identity for MCP tools (server-side)
    └── _module_impl/         # Private implementation (loader, selector, instance decision, metadata, ctx merger)

The concrete module implementations live in plugin packages:
    plugins/builtin.<id>/src/narranexus_plugins/<pkg>/   (import name narranexus_plugins.<pkg>)

Usage:
    >>> from narranexus.platform.module_system import ModuleService, XYZBaseModule
    >>> service = ModuleService(agent_id, user_id, db_client)
"""

# =============================================================================
# Base class (imported from base.py)
# =============================================================================
from typing import Optional

from narranexus.platform.schema.module_schema import ModuleConfig

from .base import XYZBaseModule, mcp_base_url, mcp_host, mcp_mount_path, mcp_port, mcp_server_url

# Caller identity, both sides. Published here so callers OUTSIDE this package
# do not reach into a private module: the INJECTION side for context_runtime
# (it builds the per-agent mcp spec), and — since batch 6b moved every MCP
# tool into a plugin package — the SERVER side too. The resolvers used to be
# deliberately private, which stopped being defensible the moment the tools
# that must call them shipped as separate wheels: seven plugin call sites were
# importing ``module_system._mcp_identity`` directly.
from ._mcp_identity import (
    AGENT_ID_HEADER,
    TURN_SOURCE_HEADER,
    USER_ID_HEADER,
    ERRAND_PEER_HEADER,
    ERRAND_CHANNEL_HEADER,
    ROOT_RUN_ID_HEADER,
    TEAM_ID_HEADER,
    EVENT_ID_HEADER,
    IDENTITY_TOKEN_HEADER,
    BEARER_AGENT_PREFIX,
    agent_id_headers,
    caller_errand_scope,
    caller_event_id_from_request,
    caller_root_run_id,
    caller_team_id_from_request,
    caller_turn_source,
    caller_user_id_from_request,
    parse_bearer_identity,
    resolve_caller_agent_id,
    stamp_identity_token,
)

# =============================================================================
# Concrete Module implementations (must be after XYZBaseModule definition)
# =============================================================================

# Module mapping table.
from narranexus.platform.module_system.registry import ModuleRegistry, module_registry

# The builtin modules are registered by the host boot from their manifests
# (agent.capabilities.modules); ``module_registry`` is the live VIEW of that
# registry: a builtin disabled through registry.json's builtin_overrides
# disappears from it, and from every derived view, at boot. Nothing here
# registers at import.


def module_config(module_class: str) -> "Optional[ModuleConfig]":
    """The module's own declaration (``ModuleConfig``) by class name, None when unknown.

    The one lookup behind every former constant table (display, decision meta,
    default / base / always-load sets, instance prefix, role) — a plugin module
    is described exactly like a builtin because it declares the same fields.
    """
    cls = module_registry.get(module_class)
    return cls.get_config() if cls else None


def module_configs() -> "dict[str, ModuleConfig]":
    """Every registered module's declaration, keyed by class name."""
    return {name: cls.get_config() for name, cls in module_registry.items()}


def is_task_module(module_class: str) -> bool:
    """A task-type module (created by the instance decision, deleted when done — JobModule)."""
    cfg = module_config(module_class)
    return bool(cfg and cfg.module_type == "task")


def module_by_role(role: str) -> "Optional[str]":
    """Class name of the module declaring ``role`` ("chat", "awareness", "social_network", "jobs"), None if absent."""
    for name, cfg in module_configs().items():
        if cfg.role == role:
            return name
    return None


def instance_prefix_for(module_class: str) -> str:
    """Instance-id prefix for a module class (its declaration, else the class name minus ``Module``)."""
    cfg = module_config(module_class)
    return cfg.effective_instance_prefix() if cfg else (module_class.lower().replace("module", "") or "inst")


def module_class_provides_chat_history(module_class: str) -> bool:
    """Capability lookup by module-class name (iron rule #4 / decoupling).

    The pipeline stores instances by `module_class` string, so it can't call
    a method on a live object. This maps the stored name to the module's
    `provides_chat_history()` capability flag via module_registry, letting the
    orchestration layer find the chat-bearing module without hard-coding
    `== "ChatModule"`. Unknown names → False.
    """
    cls = module_registry.get(module_class)
    return bool(cls and cls.provides_chat_history())


# =============================================================================
# Rebuild ModuleInstance model to resolve forward references
# =============================================================================
from narranexus.platform.schema.module_schema import rebuild_module_instance_model
rebuild_module_instance_model()

# =============================================================================
# Core services (protocol layer)
# =============================================================================
from .module_service import ModuleService
from .hook_manager import HookManager

# =============================================================================
# Public interface for private implementations
# =============================================================================
from ._module_impl import (
    ModuleSelector,
    ContextDataMerger,
    # Instance factory and decision
    InstanceFactory,
    generate_instance_id,
    InstanceDict,
    JobConfig,
    # Metadata utility functions
    get_module_metadata,
    get_all_modules_metadata,
    get_available_module_names,
)

# =============================================================================
# Public API
# =============================================================================
__all__ = [
    "AGENT_ID_HEADER",
    "TURN_SOURCE_HEADER",
    "USER_ID_HEADER",
    "ERRAND_PEER_HEADER",
    "ERRAND_CHANNEL_HEADER",
    "ROOT_RUN_ID_HEADER",
    "TEAM_ID_HEADER",
    "EVENT_ID_HEADER",
    "IDENTITY_TOKEN_HEADER",
    "BEARER_AGENT_PREFIX",
    "agent_id_headers",
    "caller_errand_scope",
    "caller_event_id_from_request",
    "caller_root_run_id",
    "caller_team_id_from_request",
    "caller_turn_source",
    "caller_user_id_from_request",
    "parse_bearer_identity",
    "resolve_caller_agent_id",
    "stamp_identity_token",
    # ===== Base class =====
    "XYZBaseModule",

    # ===== Module mapping =====
    "module_registry",
    "ModuleRegistry",
    "module_class_provides_chat_history",
    "module_config",
    "module_configs",
    "is_task_module",
    "module_by_role",
    "instance_prefix_for",

    # ===== Core services =====
    "ModuleService",
    "HookManager",

    # ===== Utility classes =====
    "ModuleSelector",
    "ContextDataMerger",

    # ===== Instance factory and decision =====
    "InstanceFactory",
    "generate_instance_id",
    "InstanceDict",
    "JobConfig",

    # ===== Metadata utilities =====
    "get_module_metadata",
    "get_all_modules_metadata",
    "get_available_module_names",
]
