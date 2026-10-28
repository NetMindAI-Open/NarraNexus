"""
@file_name: selector.py
@author: NetMind.AI
@date: 2025-12-22
@description: Module utility class

Provides Module-related utility methods.

Note: Module intelligent selection is handled by llm_decide_instances() in instance_decision.py,
which performs finer-grained management at the Instance level, including:
- Instance addition/removal/status management
- Execution path decision (AGENT_LOOP / DIRECT_TRIGGER)
- Dependency orchestration
"""

from typing import List

from .metadata import get_available_module_names


class ModuleSelector:
    """
    Module utility class

    Provides Module-related utility methods.
    Intelligent selection functionality has been migrated to llm_decide_instances() in instance_decision.py.
    """

    def __init__(self):
        """
        Initialize ModuleSelector
        """
        pass

    def get_all_modules(self) -> List[str]:
        """
        Get all available Module names

        Returns:
            List of all Module names
        """
        return get_available_module_names()

    def get_base_modules(self) -> List[str]:
        """
        Get base module list

        Returns:
            List of base module names
        """
        from narranexus.platform.module_system import module_configs

        return sorted(name for name, cfg in module_configs().items() if cfg.base)
