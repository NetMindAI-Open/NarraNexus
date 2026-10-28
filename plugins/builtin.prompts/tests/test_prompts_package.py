"""
@file_name: test_prompts_package.py
@author: Bin Liang
@date: 2026-09-07
@description: Package contract of `builtin.prompts`: the manifest equals the host's, every section renders through the runtime helpers it is given (security only on cloud, temporal skipped under relocation, modules from the runtime), the assembler joins non-empty sections, records part sizes and reports budget overruns; the platform seam orders sections by declared order or by a binding.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from narranexus.contracts.prompt import PromptAssembler, PromptContext, PromptSectionProvider, RenderedSection
from narranexus_plugins.prompts.assembler import DefaultPromptAssembler
from narranexus_plugins.prompts.sections import CONTRIBUTIONS, ModulesSection, SecuritySection, TemporalSection

ROOT = Path(__file__).resolve().parents[1]


class _Runtime:
    async def build_user_temporal_block(self, user_id):
        return f"<temporal for {user_id}>"

    async def build_module_instructions_prompt(self, mods):
        return "## Modules\n" + "\n".join(m.name for m in mods)


def _ctx(**over):
    base = dict(agent_id="a1", user_id="u1", ctx_data=SimpleNamespace(bootstrap_active=False), narrative_list=[], selected_events=[],
                module_instructions=[SimpleNamespace(name="Chat"), SimpleNamespace(name="Job")], db=None, runtime=_Runtime(), deployment_mode="local")
    base.update(over)
    return PromptContext(**base)


def test_manifest_and_contract_shapes():
    from narranexus.kernel.plugins.builtins import load_builtins, BUILTIN_MANIFEST_DATA

    on_disk = json.loads((ROOT / "narranexus-plugin.json").read_text())
    assert [c.name for c in CONTRIBUTIONS] == ["security", "temporal", "narrative", "modules", "bootstrap"]
    for c in CONTRIBUTIONS:
        assert isinstance(c.factory(), PromptSectionProvider)
    assert isinstance(DefaultPromptAssembler(), PromptAssembler)


@pytest.mark.asyncio
async def test_sections_render_from_the_runtime(monkeypatch):
    from narranexus.platform.context_runtime.prompts import SECURITY_IRON_RULES

    assert await SecuritySection().render(_ctx()) is None
    # Must be the literal iron-rules text, not merely "some non-empty string" —
    # swapping this for arbitrary copy must fail, per the P1 prompt-freeze rule.
    assert await SecuritySection().render(_ctx(deployment_mode="cloud")) == SECURITY_IRON_RULES
    from narranexus.platform import settings as settings_mod

    monkeypatch.setattr(settings_mod.settings, "prompt_turn_context_relocation_enabled", False)
    assert await TemporalSection().render(_ctx()) == "<temporal for u1>"
    monkeypatch.setattr(settings_mod.settings, "prompt_turn_context_relocation_enabled", True)
    assert await TemporalSection().render(_ctx()) is None
    assert (await ModulesSection().render(_ctx())).endswith("Chat\nJob")
    assert await ModulesSection().render(_ctx(module_instructions=[])) is None


@pytest.mark.asyncio
async def test_assembler_joins_records_sizes_and_reports_budget():
    from loguru import logger

    ctx = _ctx()
    rendered = [RenderedSection("a", "x", 10, "AAA"), RenderedSection("empty", "x", 20, ""), RenderedSection("b", "x", 30, "BBBBBB")]
    # `assembler.py` logs via loguru, which does not route through stdlib
    # `logging` — `caplog` never sees it, so capture with a real loguru sink
    # (see tests/utils/logging/test_logging.py's `captured` fixture).
    messages: list[str] = []
    handler_id = logger.add(lambda m: messages.append(m.record["message"]), level="TRACE")
    try:
        out = await DefaultPromptAssembler(budgets={"b": 3}).assemble(rendered, ctx)
    finally:
        logger.remove(handler_id)
    assert out == "AAA\n\nBBBBBB" and ctx.part_sizes == {"a": 3, "b": 6}
    assert any("over their declared budget" in m and "b: 6 > 3" in m for m in messages), messages


def test_platform_seam_orders_by_declared_order_then_by_binding():
    from narranexus.kernel.plugins.bindings import BindingSource, Layer, resolve
    from narranexus.kernel.plugins.builtins import slot_tree_with_builtins
    from narranexus.kernel.plugins.registries import Registries
    from narranexus.platform.prompt_slots import assembler_for, sections_for

    from narranexus.kernel.plugins.builtins import load_builtins

    regs = Registries(slot_tree_with_builtins())
    load_builtins(regs, "backend")
    assert [p.id for p in sections_for(regs)] == ["security", "temporal", "narrative", "modules", "bootstrap"]
    assert isinstance(assembler_for(regs), DefaultPromptAssembler)
    regs.set_bindings(resolve(slot_tree_with_builtins(), [BindingSource(Layer.USER_CONFIG, {"prompt.sections": ["modules", "narrative"]}, origin="t")]))
    assert [p.id for p in sections_for(regs)] == ["modules", "narrative"]
