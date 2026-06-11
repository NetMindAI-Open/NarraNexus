"""
@file_name: __init__.py
@author: NetMind.AI
@date: 2025-12-22
@description: Unified exports for the Module package

Module structure (after refactoring):
    module/
    ├── __init__.py           # This file - unified exports
    ├── base.py               # XYZBaseModule base class
    ├── module_service.py     # Module service (protocol layer)
    ├── hook_manager.py       # Hook manager
    ├── module_runner.py      # MCP runner
    ├── _module_impl/         # Private implementation
    │   ├── loader.py         # Module loading
    │   ├── selector.py       # Module selection
    │   ├── instance_decision.py # Instance decision
    │   ├── metadata.py       # Metadata
    │   └── ctx_merger.py     # ContextData merging
    └── *_module/             # Concrete module implementations

Usage:
    >>> from xyz_agent_context.module import ModuleService, XYZBaseModule
    >>> service = ModuleService(agent_id, user_id, db_client)
"""

# =============================================================================
# Base class (imported from base.py)
# =============================================================================
from .base import XYZBaseModule, mcp_host

# =============================================================================
# Concrete Module implementations (must be after XYZBaseModule definition)
# =============================================================================
from xyz_agent_context.module.awareness_module.awareness_module import AwarenessModule
from xyz_agent_context.module.basic_info_module.basic_info_module import BasicInfoModule
from xyz_agent_context.module.chat_module.chat_module import ChatModule
from xyz_agent_context.module.social_network_module.social_network_module import SocialNetworkModule
from xyz_agent_context.module.job_module.job_module import JobModule
from xyz_agent_context.module.skill_module.skill_module import SkillModule
from xyz_agent_context.module.message_bus_module.message_bus_module import MessageBusModule
from xyz_agent_context.module.lark_module.lark_module import LarkModule
from xyz_agent_context.module.slack_module.slack_module import SlackModule
from xyz_agent_context.module.telegram_module.telegram_module import TelegramModule
from xyz_agent_context.module.common_tools_module.common_tools_module import CommonToolsModule
from xyz_agent_context.module.general_memory_module.general_memory_module import GeneralMemoryModule

# Module mapping table.
MODULE_MAP = {
    "AwarenessModule": AwarenessModule,
    "BasicInfoModule": BasicInfoModule,
    "ChatModule": ChatModule,
    "SocialNetworkModule": SocialNetworkModule,
    "JobModule": JobModule,
    "SkillModule": SkillModule,
    "MessageBusModule": MessageBusModule,
    "LarkModule": LarkModule,
    "SlackModule": SlackModule,
    "TelegramModule": TelegramModule,
    "CommonToolsModule": CommonToolsModule,
    "GeneralMemoryModule": GeneralMemoryModule,
}


def module_class_provides_chat_history(module_class: str) -> bool:
    """Capability lookup by module-class name (iron rule #4 / decoupling).

    The pipeline stores instances by `module_class` string, so it can't call
    a method on a live object. This maps the stored name to the module's
    `provides_chat_history()` capability flag via MODULE_MAP, letting the
    orchestration layer find the chat-bearing module without hard-coding
    `== "ChatModule"`. Unknown names → False.
    """
    cls = MODULE_MAP.get(module_class)
    return bool(cls and cls.provides_chat_history())


# =============================================================================
# Rebuild ModuleInstance model to resolve forward references
# =============================================================================
from xyz_agent_context.schema.module_schema import rebuild_module_instance_model
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
    # ===== Base class =====
    "XYZBaseModule",

    # ===== Concrete modules =====
    "MemoryModule",
    "AwarenessModule",
    "BasicInfoModule",
    "ChatModule",
    "SocialNetworkModule",
    "JobModule",
    "SkillModule",
    "MessageBusModule",
    "LarkModule",
    "SlackModule",
    "TelegramModule",
    "CommonToolsModule",

    # ===== Module mapping =====
    "MODULE_MAP",
    "module_class_provides_chat_history",

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
