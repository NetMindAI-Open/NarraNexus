"""
@file_name: test_skill_scan_order.py
@author: NarraNexus
@date: 2026-07-28
@description: R4d — SkillModule directory iteration must be name-sorted, not
raw readdir order.

The skills table reaches the system prompt (hook_data_gathering ->
ctx_data.extra_data["skills_table"] -> get_instructions), and
``Path.iterdir()`` yields whatever order the filesystem hands back. APFS is
NOT alphabetical: a live workspace listed as
``officecli, home-assistant-setup, netmind-transcribe, netmind-vision``.
Because ``_materialize_builtin_skills()`` runs every round and the agent can
create/remove skill directories mid-conversation, any such change reshuffles
the whole table at IDENTICAL total length — a same-length reorder that is
invisible to every byte-count diagnostic and that punctures the cacheable
system-prompt prefix at the first transposed row.

These tests permute what ``iterdir()`` returns and require the rendered
instructions to come out byte-identical.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from xyz_agent_context.module.skill_module.skill_module import (
    SKILL_INSTRUCTIONS_TEMPLATE,
    SkillModule,
)
from xyz_agent_context.schema import ContextData

# The APFS order observed on a real workspace (non-alphabetical on purpose).
_APFS_ORDER = [
    "officecli",
    "home-assistant-setup",
    "netmind-transcribe",
    "netmind-vision",
]


def _make_skills_dir(tmp_path: Path) -> Path:
    skills_dir = tmp_path / "skills"
    skills_dir.mkdir()
    for name in _APFS_ORDER:
        skill_dir = skills_dir / name
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: does {name} things\n---\n\nBody text.\n",
            encoding="utf-8",
        )
    return skills_dir


def _module(skills_dir: Path) -> SkillModule:
    """A SkillModule wired to `skills_dir` without touching DB / settings.

    __new__ + explicit attributes: the real __init__ derives skills_dir from
    settings.base_working_path and needs a database client, neither of which
    this test is about.
    """
    mod = SkillModule.__new__(SkillModule)
    mod.agent_id = "agent_skill_order"
    mod.user_id = "user_skill_order"
    mod.db = None
    mod.skills_dir = skills_dir
    mod.instructions = SKILL_INSTRUCTIONS_TEMPLATE
    # Built-in materialization would copy the repo's vendored skills into the
    # temp workspace; env collection needs the Fernet secret box. Neither is
    # under test — the ordering of the scan is.
    mod._materialize_builtin_skills = lambda: None  # type: ignore[method-assign]
    mod.get_all_skill_env_vars = lambda: {}  # type: ignore[method-assign]
    return mod


def _patch_iterdir(monkeypatch: pytest.MonkeyPatch, permute) -> None:
    """Make every Path.iterdir() hand back a caller-chosen permutation."""
    real_iterdir = Path.iterdir

    def fake_iterdir(self: Path):
        return iter(permute(list(real_iterdir(self))))

    monkeypatch.setattr(Path, "iterdir", fake_iterdir)


async def _render(skills_dir: Path) -> str:
    mod = _module(skills_dir)
    ctx = ContextData(
        agent_id=mod.agent_id, user_id=mod.user_id, input_content="hi"
    )
    ctx = await mod.hook_data_gathering(ctx)
    return await mod.get_instructions(ctx)


@pytest.mark.asyncio
async def test_scan_is_name_sorted_not_readdir_order(tmp_path, monkeypatch):
    """The rendered table rows follow alphabetical order even when the
    filesystem reports the APFS order."""
    skills_dir = _make_skills_dir(tmp_path)
    _patch_iterdir(monkeypatch, lambda items: sorted(
        items, key=lambda p: _APFS_ORDER.index(p.name) if p.name in _APFS_ORDER else -1
    ))

    rendered = await _render(skills_dir)

    positions = [rendered.index(f"| {name} |") for name in sorted(_APFS_ORDER)]
    assert positions == sorted(positions), rendered


@pytest.mark.asyncio
async def test_two_different_permutations_render_byte_identical(tmp_path, monkeypatch):
    """The actual regression: two rounds whose only difference is the order
    the filesystem enumerated the same skill set must produce byte-identical
    instructions."""
    skills_dir = _make_skills_dir(tmp_path)

    _patch_iterdir(monkeypatch, lambda items: sorted(
        items, key=lambda p: _APFS_ORDER.index(p.name) if p.name in _APFS_ORDER else -1
    ))
    round_a = await _render(skills_dir)

    # A different permutation of the SAME set (e.g. after a skill was added
    # and removed again, which reshuffles APFS readdir order).
    _patch_iterdir(monkeypatch, lambda items: sorted(items, reverse=True, key=lambda p: p.name))
    round_b = await _render(skills_dir)

    assert round_a == round_b
    # And the reorder really was same-length — the class of divergence no
    # byte-count diagnostic can see.
    assert len(round_a) == len(round_b)


@pytest.mark.asyncio
async def test_added_skill_does_not_reshuffle_existing_rows(tmp_path, monkeypatch):
    """Adding a skill mid-conversation may legally extend the table; it must
    not permute the rows that were already there."""
    skills_dir = _make_skills_dir(tmp_path)
    _patch_iterdir(monkeypatch, lambda items: sorted(items, reverse=True, key=lambda p: p.name))

    before = await _render(skills_dir)

    new_dir = skills_dir / "zz-late-arrival"
    new_dir.mkdir()
    (new_dir / "SKILL.md").write_text(
        "---\nname: zz-late-arrival\ndescription: added mid-conversation\n---\n",
        encoding="utf-8",
    )
    after = await _render(skills_dir)

    order_before = [n for n in sorted(_APFS_ORDER) if f"| {n} |" in before]
    order_after = [n for n in sorted(_APFS_ORDER) if f"| {n} |" in after]
    assert order_before == order_after
    assert "zz-late-arrival" in after
