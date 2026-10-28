"""
Module private implementation

This directory contains the concrete implementations of ModuleService and should not be directly imported externally.

Module list:
- loader: Module loading logic
- selector: Dynamic module selection
- instance_decision: LLM Instance decision
- instance_factory: Instance creation factory
- metadata: Module metadata
- ctx_merger: ContextData merging
"""

from .loader import ModuleLoader
from .selector import ModuleSelector
from .instance_decision import (
    llm_decide_instances,
    dict_to_module_instance,
    InstanceDecisionOutput,
    InstanceDict,
    JobConfig,
)
from .instance_factory import (
    InstanceFactory,
    generate_instance_id,
)
from .metadata import (
    get_module_metadata,
    get_all_modules_metadata,
    get_available_module_names,
    get_persistent_modules,
    get_task_modules,
)
from .ctx_merger import ContextDataMerger

__all__ = [
    # Loader
    "ModuleLoader",
    # Selector
    "ModuleSelector",
    # Instance Decision
    "llm_decide_instances",
    "dict_to_module_instance",
    "InstanceDecisionOutput",
    "InstanceDict",
    "JobConfig",
    # Instance Factory
    "InstanceFactory",
    "generate_instance_id",
    # Metadata
    "get_module_metadata",
    "get_all_modules_metadata",
    "get_available_module_names",
    "get_persistent_modules",
    "get_task_modules",
    # Merger
    "ContextDataMerger",
]
