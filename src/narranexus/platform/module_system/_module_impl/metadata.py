"""
@file_name: metadata.py
@author: NetMind.AI
@date: 2025-12-22
@description: Module metadata — rendered from every module's own ``ModuleConfig.decision`` (plugin platform batch 5b).

Used for:
1. Describing each module's capabilities to the instance-decision LLM
2. Reference information during Instance management
3. Documentation generation

There is no table here any more: a builtin and a plugin module both declare
``ModuleDecisionMeta`` in ``get_config()`` and appear in the same rendering.
"""

from typing import Any, Dict, List


def _decision_entries() -> Dict[str, Dict[str, Any]]:
    from narranexus.platform.module_system import module_configs

    out: Dict[str, Dict[str, Any]] = {}
    for name, cfg in sorted(module_configs().items(), key=lambda kv: (kv[1].priority, kv[0])):
        if cfg.decision is None:
            continue
        d = cfg.decision
        out[name] = {
            "name": name,
            "description": cfg.description,
            "capabilities": list(d.capabilities),
            "instance_type": d.instance_type,
            "typical_instance_id": d.typical_instance_id or f"{cfg.effective_instance_prefix()}_{{uuid8}}",
            "use_cases": list(d.use_cases),
            "priority": cfg.priority,
        }
    return out


def get_module_metadata(module_name: str) -> Dict[str, Any]:
    """Metadata for one module (empty dict when it declares no decision meta)."""
    return _decision_entries().get(module_name, {})


def get_all_modules_metadata() -> str:
    """Every module's decision metadata, formatted as LLM-readable text."""
    lines = []
    for module_name, metadata in _decision_entries().items():
        lines.append(f"## {module_name}")
        lines.append(f"- **Description**: {metadata['description']}")
        lines.append(f"- **Type**: {metadata['instance_type']} ({'persistent, retained once added' if metadata['instance_type'] == 'persistent' else 'task-type, deleted after completion'})")
        lines.append(f"- **Typical Instance ID**: `{metadata['typical_instance_id']}`")
        lines.append(f"- **Priority**: {metadata['priority']}")
        lines.append("- **Capabilities**:")
        for cap in metadata['capabilities']:
            lines.append(f"  - {cap}")
        lines.append("- **Use Cases**:")
        for use_case in metadata['use_cases']:
            lines.append(f"  - {use_case}")
        lines.append("")
    return "\n".join(lines)


def get_available_module_names() -> List[str]:
    """Every registered module class name."""
    from narranexus.platform.module_system import module_registry

    return list(module_registry)


def get_persistent_modules() -> List[str]:
    return [name for name, meta in _decision_entries().items() if meta.get("instance_type") == "persistent"]


def get_task_modules() -> List[str]:
    return [name for name, meta in _decision_entries().items() if meta.get("instance_type") == "task"]
