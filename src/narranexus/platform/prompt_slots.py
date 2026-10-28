"""
@file_name: prompt_slots.py
@author: Bin Liang
@date: 2026-09-07
@description: The platform's seam to the prompt slots: ``sections_for`` / ``assembler_for`` hand back the bound providers through ``kernel.plugins.bound`` so a distribution's or narranexus.toml's binding decides the order, the drops and the assembler.
"""
from __future__ import annotations

from typing import Any

from loguru import logger

from narranexus.contracts.prompt import PromptAssembler, PromptSectionProvider
from narranexus.kernel.plugins.bound import bound_entries, bound_entry
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

SECTIONS_SLOT = "prompt.sections"
ASSEMBLER_SLOT = "prompt.assembler"


def sections_for(registries: Any = None) -> list[PromptSectionProvider]:
    """The section providers in effect: binding order when bound, else declared ``order``.

    The registries are populated by the host boot (builtin.prompts' manifest);
    an empty slot answers an empty list, never a silently re-registered one — an
    unbooted process must fail visibly rather than grow a prompt from nowhere.

    A section that is LOAD-BEARING for a deployment mode says so itself
    (``PromptSectionProvider.required_in``) and the render loop in
    ``context_runtime`` refuses to produce a prompt without it. That covers a
    required section that renders empty or raises; a distribution that removes
    the PROVIDER entirely is out of reach here (there is nothing left to ask),
    so the empty slot is logged loudly instead."""
    regs = registries or KERNEL_REGISTRIES
    entries = bound_entries(regs, SECTIONS_SLOT)
    if not entries:
        logger.error(
            f"[prompt] {SECTIONS_SLOT} is empty — every turn's system prompt will be blank. "
            f"Has this process booted the plugin platform, and does the distribution include a "
            f"prompt-section plugin?"
        )
    providers = [e.factory() for e in entries]
    resolved = getattr(regs, "bindings", None)
    if resolved is None or SECTIONS_SLOT not in resolved.many or not resolved.many[SECTIONS_SLOT].providers:
        providers.sort(key=lambda p: getattr(p, "order", 100))
    return providers


def assembler_for(registries: Any = None) -> PromptAssembler:
    regs = registries or KERNEL_REGISTRIES
    return bound_entry(regs, ASSEMBLER_SLOT).factory()


__all__ = ["ASSEMBLER_SLOT", "SECTIONS_SLOT", "assembler_for", "sections_for"]
