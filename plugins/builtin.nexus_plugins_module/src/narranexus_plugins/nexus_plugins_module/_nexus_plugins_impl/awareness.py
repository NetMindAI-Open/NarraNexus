"""
@file_name: awareness.py
@author: Bin Liang
@date: 2026-09-07
@description: What an agent can learn about the platform it runs on and about itself (Owner ask 2026-09-07: "感知要更好"): the distribution and loaded plugins, the slot catalog by domain with what is bound, contract documentation on demand, its own capability switches / model slots / prompt sections — and one write: switching one of its own capabilities (owner-checked, like the REST route).
"""
from __future__ import annotations

import importlib
import inspect
from typing import Any, Optional

from narranexus.kernel.deployment import get_deployment_mode
from narranexus.kernel.plugins.compat import host_version
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES


def platform_overview() -> dict[str, Any]:
    from narranexus.kernel.plugins.builtins import builtin_manifests
    from narranexus.kernel.plugins.catalog import slot_catalog
    from narranexus.kernel.plugins.distribution import find_distribution, load_distribution
    from narranexus.kernel.plugins.lifecycle import RegistryStore
    from narranexus.kernel.plugins.paths import registry_path

    dist: Optional[dict[str, Any]] = None
    path = find_distribution()
    if path is not None:
        try:
            spec, _ = load_distribution(path)
            dist = {"id": spec.id, "displayName": spec.display_name, "deployment": spec.runtime.deployment, "auth": spec.auth,
                    "userPlugins": spec.runtime.user_plugins, "plugins": list(spec.plugin_ids), "excludes": list(spec.excludes)}
        except Exception as exc:  # noqa: BLE001
            dist = {"error": str(exc)}
    store = RegistryStore(path=registry_path())
    user_plugins: dict[str, Any] = {}
    safe_mode = False
    if store.path.exists():
        reg = store.read()
        safe_mode = reg.safe_mode
        user_plugins = {pid: {"version": r.installed_version, "state": r.state, "enabled": r.enabled, "scope": r.scope} for pid, r in sorted(reg.plugins.items())}
    catalog = slot_catalog(KERNEL_REGISTRIES)
    bound_non_default = [
        {"slot": s["path"], **s["bound"]}
        for g in catalog for s in g["slots"]
        if (s["arity"] == "one" and s["bound"].get("layer") not in (None, "DEFAULT")) or (s["arity"] == "many" and s["bound"].get("providers"))
    ]
    return {
        "host_version": host_version(),
        "deployment_mode": get_deployment_mode(),
        "distribution": dist,
        "builtin_plugins": [{"id": m.id, "version": m.version, "hosts": list(m.hosts), "quality": getattr(m, "quality", None), "description": m.description} for m in builtin_manifests()],
        "user_plugins": user_plugins,
        "safe_mode": safe_mode,
        "slot_domains": [{"domain": g["domain"], "title": g["title"], "slots": len(g["slots"])} for g in catalog],
        "bindings_not_default": bound_non_default,
        "how_to_go_deeper": "platform_slots(domain) for the slots of one domain; contract_docs(kind) for a contract; plugin_docs(kinds) for templates and examples.",
    }


def platform_slots(domain: str = "") -> list[dict[str, Any]]:
    from narranexus.kernel.plugins.catalog import slot_catalog

    return slot_catalog(KERNEL_REGISTRIES, domain=domain or None)


def contract_docs(kind: str) -> dict[str, Any]:
    """The kind's contract symbol(s) with their docstrings and public methods, plus the slots that carry the kind."""
    from narranexus.contracts import API_VERSIONS, STABILITY

    if kind not in API_VERSIONS:
        return {"error": f"unknown kind {kind!r}", "kinds": sorted(API_VERSIONS)}
    slots = [s.path for s in KERNEL_REGISTRIES.slots.by_kind(kind)]
    tree = KERNEL_REGISTRIES.slots
    contracts: dict[str, Any] = {}
    for path in slots:
        if path not in tree:
            continue
        symbol = tree.get(path).contract
        if symbol in contracts:
            continue
        mod_name, _, attr = symbol.partition(":")
        try:
            obj = getattr(importlib.import_module(mod_name), attr)
            members = [f"{n}{inspect.signature(m)}" for n, m in inspect.getmembers(obj) if not n.startswith("_") and callable(m)]
            contracts[symbol] = {"doc": inspect.getdoc(obj) or "", "members": members[:20]}
        except Exception as exc:  # noqa: BLE001
            contracts[symbol] = {"error": str(exc)}
    return {"kind": kind, "version": API_VERSIONS[kind], "stability": STABILITY[kind].value, "slots": slots, "contracts": contracts}


async def agent_self(db: Any, agent_id: str, user_id: str) -> dict[str, Any]:
    from narranexus.platform.agent_framework.providers.slot_service import AgentSlotService
    from narranexus.platform.module_system.capability_service import CapabilityService
    from narranexus.platform.prompt_slots import sections_for
    from narranexus.platform.repository import AgentRepository

    agent = await AgentRepository(db).get_agent(agent_id)
    caps = await CapabilityService(db, KERNEL_REGISTRIES).overview(agent_id)
    try:
        slots = await AgentSlotService(db).get_agent_slots(agent_id)
    except Exception as exc:  # noqa: BLE001
        slots = {"error": str(exc)}
    sections = [{"id": p.id, "order": getattr(p, "order", None), "budget_chars": getattr(p, "budget_chars", 0)} for p in sections_for(KERNEL_REGISTRIES)]
    return {
        # no owner id: the agent has no use for it, and handing it to the model
        # is what made a self-reported user_id a way through the owner check
        "agent": {"agent_id": agent_id, "name": getattr(agent, "agent_name", None), "description": getattr(agent, "agent_description", None), "is_owner": getattr(agent, "created_by", None) == user_id},
        "capabilities": caps,
        "model_slots": slots,
        "prompt_sections": sections,
        "how_to_change": "capability_set(module_class, enabled) switches one of your capabilities (owner only, applies next turn); model slots are the user's in Settings.",
    }


async def capability_set(db: Any, agent_id: str, user_id: Optional[str], module_class: str, enabled: bool) -> dict[str, Any]:
    """``user_id`` is the INJECTED caller identity (never a tool argument): the
    owner check must not run on a value the model can type in."""
    from narranexus.platform.module_system.capability_service import CapabilityService
    from narranexus.platform.repository import AgentRepository

    if not user_id:
        return {"error": "caller identity unavailable; capability changes are refused (fail-closed)"}
    agent = await AgentRepository(db).get_agent(agent_id)
    if agent is None or agent.created_by != user_id:
        return {"error": "only the agent's owner may change its capabilities"}
    svc = CapabilityService(db, KERNEL_REGISTRIES)
    if svc.is_locked(module_class):
        return {"error": f"{module_class} is a base capability and stays on"}
    result = await svc.set_enabled(agent_id, module_class, enabled)
    return {"module_class": module_class, "enabled": enabled, "result": result, "applies": "next turn"}


__all__ = ["agent_self", "capability_set", "contract_docs", "platform_overview", "platform_slots"]
