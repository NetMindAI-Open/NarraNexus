"""
@file_name: test_required_prompt_sections.py
@author: Bin Liang
@date: 2026-09-07
@description: A prompt section that is REQUIRED in this deployment mode fails the prompt when it renders nothing; an optional one still degrades per-section.

Round-2 P2-I4: the prompt became a slot and inherited the registry's
isolate-per-entry reflex, so the cloud security preamble (the 2026-06-17
incident's mitigation) was droppable by a binding, by a ``builtin_overrides``
disable, or by one raising provider — with a single warning and no visible
difference in ``[SYSPROMPT-BREAKDOWN]``. Revert ``required_in`` handling in
``context_runtime.build_complete_system_prompt`` and
``test_cloud_security_section_is_load_bearing`` /
``test_a_raising_required_section_refuses_the_prompt`` go green-to-red.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Optional

import pytest

from narranexus.contracts.prompt import (
    PromptContext,
    RequiredSectionMissing,
    section_required,
)
from narranexus.platform.context_runtime.context_runtime import (
    MISSING_SECTION_SIZE,
    ContextRuntime,
)


class _Section:
    """A minimal PromptSectionProvider under test control."""

    def __init__(self, sid: str, text: Optional[str], *, required_in: tuple[str, ...] = (), boom: bool = False):
        self.id = sid
        self.order = 10
        self.budget_chars = 0
        self.required_in = required_in
        self._text = text
        self._boom = boom

    async def render(self, ctx: PromptContext) -> Optional[str]:
        if self._boom:
            raise RuntimeError("section exploded")
        return self._text


class _Assembler:
    async def assemble(self, sections, ctx) -> str:
        return "\n".join(s.text for s in sections)


def _runtime() -> ContextRuntime:
    rt = ContextRuntime.__new__(ContextRuntime)
    rt.agent_id = "a1"
    rt.db = None
    return rt


def _build(monkeypatch, sections, mode: str) -> str:
    monkeypatch.setattr("narranexus.platform.prompt_slots.sections_for", lambda registries=None: sections)
    monkeypatch.setattr("narranexus.platform.prompt_slots.assembler_for", lambda registries=None: _Assembler())
    monkeypatch.setattr("narranexus.platform.utils.deployment_mode.get_deployment_mode", lambda: mode)

    rt = _runtime()
    monkeypatch.setattr(rt, "_settle_bootstrap", _noop, raising=False)
    ctx_data = SimpleNamespace(user_id="u1", bootstrap_active=False)
    return asyncio.run(rt.build_complete_system_prompt([], [], [], ctx_data))


async def _noop(*args, **kwargs):
    return None


def test_section_required_reads_the_deployment_mode():
    cloud_only = _Section("security", "rules", required_in=("cloud",))
    assert section_required(cloud_only, "cloud")
    assert not section_required(cloud_only, "local")
    # A provider written against the older Protocol has no attribute at all and
    # must be treated as degradable rather than crashing the loop.
    assert not section_required(SimpleNamespace(id="legacy"), "cloud")


def test_cloud_security_section_is_load_bearing(monkeypatch):
    """Empty in a mode that requires it → no prompt at all, and the breakdown
    line names the section."""
    lines: list[str] = []
    monkeypatch.setattr(
        "narranexus.platform.context_runtime.context_runtime.logger",
        SimpleNamespace(
            info=lambda m: lines.append(m),
            debug=lambda m: None,
            warning=lambda m: lines.append(m),
            error=lambda m: lines.append(m),
        ),
    )
    sections = [
        _Section("security", None, required_in=("cloud",)),
        _Section("narrative", "story"),
    ]
    with pytest.raises(RequiredSectionMissing) as exc:
        _build(monkeypatch, sections, "cloud")
    assert exc.value.section_id == "security" and exc.value.deployment_mode == "cloud"
    assert any("[SYSPROMPT-BREAKDOWN]" in ln and "security=MISSING" in ln for ln in lines), lines


def test_the_same_empty_section_is_fine_where_it_is_not_required(monkeypatch):
    """SecuritySection returns None on desktop BY DESIGN — a plain
    ``required: bool`` would have broken every local turn."""
    sections = [
        _Section("security", None, required_in=("cloud",)),
        _Section("narrative", "story"),
    ]
    assert _build(monkeypatch, sections, "local") == "story"


def test_a_raising_required_section_refuses_the_prompt(monkeypatch):
    sections = [_Section("security", "rules", required_in=("cloud",), boom=True)]
    with pytest.raises(RequiredSectionMissing) as exc:
        _build(monkeypatch, sections, "cloud")
    assert isinstance(exc.value.__cause__, RuntimeError)


def test_an_optional_section_keeps_its_per_section_isolation(monkeypatch):
    """The property the isolation was added for stays: one broken OPTIONAL
    section must not take the turn down."""
    sections = [_Section("boom", "x", boom=True), _Section("narrative", "story")]
    assert _build(monkeypatch, sections, "cloud") == "story"


def test_missing_sentinel_prints_as_MISSING(monkeypatch):
    lines: list[str] = []
    monkeypatch.setattr(
        "narranexus.platform.context_runtime.context_runtime.logger",
        SimpleNamespace(info=lambda m: lines.append(m), debug=lambda m: None, warning=lambda m: None),
    )
    ContextRuntime._log_system_prompt_breakdown("a1", 0, {"security": MISSING_SECTION_SIZE, "narrative": 5}, [], {})
    assert "security=MISSING" in lines[0] and "narrative=5" in lines[0]
