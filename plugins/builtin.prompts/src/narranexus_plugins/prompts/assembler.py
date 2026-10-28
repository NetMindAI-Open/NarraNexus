"""
@file_name: assembler.py
@author: Bin Liang
@date: 2026-09-07
@description: The default ``prompt.assembler``: joins non-empty sections with a blank line in the given order, records each section's size into the context's ``part_sizes`` and logs budget overruns (never truncates — a section over budget is a plugin bug to see, not to hide).
"""
from __future__ import annotations

from typing import Sequence

from loguru import logger

from narranexus.contracts.prompt import PromptAssembler, PromptContext, RenderedSection, budget_report
from narranexus.kernel.plugins.registry import Contribution


class DefaultPromptAssembler(PromptAssembler):
    id = "default"

    def __init__(self, budgets: dict[str, int] | None = None) -> None:
        self._budgets = budgets or {}

    async def assemble(self, sections: Sequence[RenderedSection], ctx: PromptContext) -> str:
        kept = [s for s in sections if s.text]
        for s in kept:
            ctx.part_sizes[s.id] = s.chars
        over = budget_report(kept, self._budgets)  # section-declared budgets, overridable by id
        if over:
            logger.warning(f"[prompt] sections over their declared budget: {', '.join(over)}")
        return "\n\n".join(s.text for s in kept).strip()


CONTRIBUTION = Contribution("default", lambda: DefaultPromptAssembler())

__all__ = ["CONTRIBUTION", "DefaultPromptAssembler"]
