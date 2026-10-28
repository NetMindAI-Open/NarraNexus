"""
@file_name: capability_service.py
@author: Bin Liang
@date: 2026-09-04
@description: Per-agent capability enablement + the context budget (plugin platform batch 5c).

Which registered modules take part in an agent's turns is the agent owner's
choice, stored per (agent, module class) in ``agent_capabilities``. The
default when no row exists is the budget rule of spec §12: a BUILTIN module
is on, a module a plugin installed is OFF for existing agents until the owner
turns it on — a newly installed plugin must not silently grow every agent's
prompt. Base modules (``ModuleConfig.base``) cannot be disabled: without chat,
awareness and basic info a turn cannot run.

The service is the single reader the loader consults (``enabled_map``) and
the single writer the owner-gated route uses (``set_enabled``); ``overview``
is the panel's view, ``budget`` the context-cost check (warning when the
enabled set exceeds 2× the builtin baseline).
"""
from __future__ import annotations

from typing import Any, Optional

from loguru import logger

from narranexus.platform.utils import utc_now
from narranexus.contracts.distribution import is_builtin_id

TABLE = "agent_capabilities"
BUDGET_WARN_RATIO = 2.0


class CapabilityService:
    def __init__(self, db: Any, registries: Any = None) -> None:
        self._db = db
        self._registries = registries

    # ---- registry views ----------------------------------------------------

    def _module_map(self):
        from narranexus.platform.module_system.registry import ModuleRegistry, module_registry

        return module_registry if self._registries is None else ModuleRegistry(self._registries)

    def owner_of(self, module_class: str) -> str:
        return self._module_map().owner_of(module_class)

    def default_enabled(self, module_class: str) -> bool:
        """The no-row default: builtin modules on, plugin modules off (context budget)."""
        return is_builtin_id(self.owner_of(module_class))

    def is_locked(self, module_class: str) -> bool:
        """Base modules stay on: a turn cannot run without them."""
        cfg = self._module_map()[module_class].get_config()
        return bool(cfg.base)

    # ---- reads -------------------------------------------------------------

    async def explicit(self, agent_id: str) -> dict[str, bool]:
        rows = await self._db.get(TABLE, {"agent_id": agent_id})
        return {str(r["capability"]): bool(r.get("enabled", 1)) for r in rows}

    async def enabled_map(self, agent_id: str) -> dict[str, bool]:
        """module class → enabled for this agent, for every registered module."""
        explicit = await self.explicit(agent_id)
        out: dict[str, bool] = {}
        for name in self._module_map():
            if self.is_locked(name):
                out[name] = True
            else:
                out[name] = explicit.get(name, self.default_enabled(name))
        return out

    async def is_enabled(self, agent_id: str, module_class: str) -> bool:
        if module_class not in self._module_map():
            return False
        if self.is_locked(module_class):
            return True
        explicit = await self.explicit(agent_id)
        return explicit.get(module_class, self.default_enabled(module_class))

    async def overview(self, agent_id: str) -> dict[str, Any]:
        """The owner's panel view: every module with its declaration, state and the budget."""
        enabled = await self.enabled_map(agent_id)
        explicit = await self.explicit(agent_id)
        items = []
        for name, cls in self._module_map().items():
            cfg = cls.get_config()
            display = cfg.display
            items.append({
                "module_class": name,
                "name": (display.name if display and display.name else name.replace("Module", "")),
                "icon": (display.icon if display else "🔌"),
                "description": cfg.description,
                "owner": self.owner_of(name),
                "builtin": is_builtin_id(self.owner_of(name)),
                "enabled": enabled[name],
                "default_enabled": self.default_enabled(name),
                "explicit": name in explicit,
                "locked": self.is_locked(name),
                "always_load": cfg.always_load,
                "context_cost_hint": cfg.context_cost_hint,
                "priority": cfg.priority,
            })
        items.sort(key=lambda i: (i["priority"], i["module_class"]))
        return {"agent_id": agent_id, "capabilities": items, "budget": self.budget(enabled)}

    def budget(self, enabled: dict[str, bool]) -> dict[str, Any]:
        """Context cost of the enabled set against the builtin baseline (declared
        ``context_cost_hint`` tokens; undeclared modules count 0)."""
        module_map = self._module_map()
        hint = {name: (cls.get_config().context_cost_hint or 0) for name, cls in module_map.items()}
        baseline = sum(h for name, h in hint.items() if self.default_enabled(name))
        total = sum(h for name, h in hint.items() if enabled.get(name))
        # No baseline (no builtin declares a context_cost_hint) but something
        # enabled: clamp instead of inf — the route serialises this and
        # `Infinity` is not JSON.
        ratio = (total / baseline) if baseline else (1.0 if not total else BUDGET_WARN_RATIO * 100)
        return {"baseline_tokens": baseline, "enabled_tokens": total, "ratio": round(ratio, 2), "over_budget": bool(baseline) and ratio > BUDGET_WARN_RATIO}

    # ---- writes ------------------------------------------------------------

    async def set_enabled(self, agent_id: str, module_class: str, enabled: bool) -> dict[str, Any]:
        if module_class not in self._module_map():
            raise ValueError(f"unknown module: {module_class}")
        if self.is_locked(module_class) and not enabled:
            raise ValueError(f"{module_class} is a base module and cannot be disabled")
        existing = await self._db.get_one(TABLE, {"agent_id": agent_id, "capability": module_class})
        if existing:
            await self._db.update(TABLE, {"agent_id": agent_id, "capability": module_class}, {"enabled": 1 if enabled else 0, "updated_at": utc_now()})
        else:
            await self._db.insert(TABLE, {"agent_id": agent_id, "capability": module_class, "enabled": 1 if enabled else 0})
        logger.info(f"[capabilities] {agent_id}: {module_class} {'enabled' if enabled else 'disabled'}")
        return {"module_class": module_class, "enabled": enabled}

    async def reset(self, agent_id: str, module_class: str) -> bool:
        """Drop the explicit row so the default rule applies again."""
        return bool(await self._db.delete(TABLE, {"agent_id": agent_id, "capability": module_class}))

    def warn_if_over_budget(self, agent_id: str, enabled: dict[str, bool]) -> Optional[dict[str, Any]]:
        b = self.budget(enabled)
        if b["over_budget"]:
            logger.warning(
                f"[capabilities] {agent_id}: enabled modules declare ~{b['enabled_tokens']} prompt tokens, "
                f"{b['ratio']}× the builtin baseline ({b['baseline_tokens']}) — over the {BUDGET_WARN_RATIO}× budget"
            )
            return b
        return None


__all__ = ["BUDGET_WARN_RATIO", "TABLE", "CapabilityService"]
