"""
@file_name: sections.py
@author: Bin Liang
@date: 2026-09-07
@description: The five default system-prompt sections, in the order the platform assembled them before the prompt became a slot: security iron rules (cloud only), the user's temporal context, the main narrative, the module instructions and the first-run bootstrap injection. Each is a ``PromptSectionProvider`` contribution; a distribution or ``narranexus.toml`` may reorder or drop them, a plugin may add its own — except where ``required_in`` says the section is load-bearing for a deployment mode (only ``security``, only on cloud).
"""
from __future__ import annotations

from typing import Optional

from loguru import logger

from narranexus.contracts.prompt import PromptContext
from narranexus.kernel.plugins.registry import Contribution

OWNER = "builtin.prompts"


class SecuritySection:
    """Cloud deployments prepend the iron rules (incident 2026-06-17); local builds do not.

    ``required_in=("cloud",)``: on cloud this section is load-bearing, so a
    binding that drops it, a ``builtin_overrides`` disable of this plugin, or
    one raising render refuses the whole prompt (``RequiredSectionMissing``)
    instead of silently shipping every turn without the iron rules. Local and
    desktop builds are unaffected — ``render`` returns ``None`` there BY DESIGN,
    which is exactly why the flag is per deployment mode rather than a plain
    ``required: bool``.
    """

    id = "security"
    order = 10
    budget_chars = 4000
    required_in = ("cloud",)

    async def render(self, ctx: PromptContext) -> Optional[str]:
        if ctx.deployment_mode != "cloud":
            return None
        from narranexus.platform.context_runtime.prompts import SECURITY_IRON_RULES

        return SECURITY_IRON_RULES


class TemporalSection:
    """User temporal context — unless the relocation setting moves it into the turn context."""

    id = "temporal"
    order = 20
    budget_chars = 2000
    # Degradable: a missing temporal block costs context, never correctness.
    required_in: tuple[str, ...] = ()

    async def render(self, ctx: PromptContext) -> Optional[str]:
        from narranexus.platform.settings import settings

        if settings.prompt_turn_context_relocation_enabled or ctx.user_id is None:
            return None
        try:
            return await ctx.runtime.build_user_temporal_block(ctx.user_id)
        except Exception as exc:  # noqa: BLE001 — a missing temporal block never breaks a turn
            logger.warning(f"        Failed to build User Temporal Context: {exc}")
            return None


class NarrativeSection:
    """The main narrative's prompt (its summary + volatile part unless relocated)."""

    id = "narrative"
    order = 30
    budget_chars = 20000
    required_in: tuple[str, ...] = ()

    async def render(self, ctx: PromptContext) -> Optional[str]:
        if not ctx.narrative_list:
            return None
        from narranexus.platform.narrative import NarrativeService
        from narranexus.platform.settings import settings

        main = ctx.narrative_list[0]
        text = await NarrativeService(ctx.agent_id).combine_main_narrative_prompt(
            main, include_volatile=not settings.prompt_turn_context_relocation_enabled
        )
        try:
            ctx.meta["nar_summary_chars"] = len(getattr(main.narrative_info, "current_summary", "") or "")
            ctx.meta["nar_dynamic_entries"] = len(getattr(main, "dynamic_summary", []) or [])
        except Exception:  # noqa: BLE001 — diagnostics must never break a turn
            pass
        return text


class ModulesSection:
    """Every enabled module's instructions (deduped by class, sorted by the runtime)."""

    id = "modules"
    order = 40
    budget_chars = 60000
    required_in: tuple[str, ...] = ()

    async def render(self, ctx: PromptContext) -> Optional[str]:
        if not ctx.module_instructions:
            return None
        return await ctx.runtime.build_module_instructions_prompt(list(ctx.module_instructions))


class BootstrapSection:
    """First-run bootstrap injection (file-read approach) for the owner's first turns.

    Pure rendering: the platform settles the Bootstrap.md lifecycle (threshold
    check, auto-delete, ``ctx_data.bootstrap_active``) BEFORE the sections
    render — this section only reads that flag. A distribution that drops the
    section loses the injected text, never the lifecycle."""

    id = "bootstrap"
    order = 50
    budget_chars = 3000
    required_in: tuple[str, ...] = ()

    async def render(self, ctx: PromptContext) -> Optional[str]:
        if not getattr(ctx.ctx_data, "bootstrap_active", False):
            return None
        from narranexus.platform.context_runtime.prompts import BOOTSTRAP_INJECTION_PROMPT

        return BOOTSTRAP_INJECTION_PROMPT


SECURITY = Contribution("security", lambda: SecuritySection())
TEMPORAL = Contribution("temporal", lambda: TemporalSection())
NARRATIVE = Contribution("narrative", lambda: NarrativeSection())
MODULES = Contribution("modules", lambda: ModulesSection())
BOOTSTRAP = Contribution("bootstrap", lambda: BootstrapSection())
CONTRIBUTIONS = (SECURITY, TEMPORAL, NARRATIVE, MODULES, BOOTSTRAP)

__all__ = ["BOOTSTRAP", "CONTRIBUTIONS", "MODULES", "NARRATIVE", "OWNER", "SECURITY", "TEMPORAL"]
