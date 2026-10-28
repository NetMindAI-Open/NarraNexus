"""
@file_name: test_lark_skill_loader.py
@author: Bin Liang
@date: 2026-04-22
@description: Tests for _lark_skill_loader.load_skill_file — path-safe
lookup, markdown link rewriting into `lark_skill(...)` MCP calls, banner
insertion, non-md pass-through, and traversal-escape rejection.

Why this file exists:
    The skill loader is the sole bridge between on-disk Lark skill
    packs and the Agent. It must (a) never leak a path outside the
    skill directory, (b) rewrite every internal markdown link so the
    Agent knows to call `lark_skill` again (not `Read`), and (c)
    return non-markdown data files untouched for cases like the
    slides XML schema.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from narranexus_plugins.lark_module import _lark_skill_loader as loader


@pytest.fixture()
def skill_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Build a mini skill tree mirroring real lark skill layouts."""
    root = tmp_path / "skills"
    root.mkdir()

    # lark-contact: SKILL.md + references/
    contact = root / "lark-contact"
    (contact / "references").mkdir(parents=True)
    (contact / "SKILL.md").write_text(
        "---\nname: lark-contact\n---\n\n"
        "# contact\n\n"
        "**CRITICAL** — 先读 [`../lark-shared/SKILL.md`](../lark-shared/SKILL.md)。\n\n"
        "| `+search-user` | [details](references/lark-contact-search-user.md) |\n"
        "| `+get-user` | [details](references/lark-contact-get-user.md) |\n",
        encoding="utf-8",
    )
    (contact / "references/lark-contact-search-user.md").write_text(
        "# search-user\n\nSee also [get-user](lark-contact-get-user.md).\n",
        encoding="utf-8",
    )
    (contact / "references/lark-contact-get-user.md").write_text(
        "# get-user\n\nReturns open_id.\n", encoding="utf-8"
    )

    # lark-shared: flat
    shared = root / "lark-shared"
    shared.mkdir()
    (shared / "SKILL.md").write_text(
        "# shared\n\nAuth rules.\n", encoding="utf-8"
    )

    # lark-whiteboard: non-`references/` subdirs
    wb = root / "lark-whiteboard"
    (wb / "routes").mkdir(parents=True)
    (wb / "scenes").mkdir(parents=True)
    (wb / "SKILL.md").write_text(
        "# whiteboard\n\n"
        "| mermaid | [routes/mermaid.md](routes/mermaid.md) |\n"
        "| funnel scene | [scenes/funnel.md](scenes/funnel.md) |\n",
        encoding="utf-8",
    )
    (wb / "routes/mermaid.md").write_text("# mermaid\n", encoding="utf-8")
    (wb / "scenes/funnel.md").write_text("# funnel\n", encoding="utf-8")

    # lark-slides: non-markdown data file
    slides = root / "lark-slides"
    (slides / "references").mkdir(parents=True)
    (slides / "SKILL.md").write_text(
        "# slides\n\nSchema: [ref](references/slides_schema.xml).\n",
        encoding="utf-8",
    )
    (slides / "references/slides_schema.xml").write_text(
        "<?xml version='1.0'?><schema><field/></schema>",
        encoding="utf-8",
    )

    # Point the loader at ONLY our fixture tree (override default
    # ~/.claude/skills + ~/.agents/skills search paths so the host's
    # real skill installs don't leak into tests).
    monkeypatch.setattr(loader, "_SKILL_SEARCH_PATHS", [root])
    monkeypatch.delenv("LARK_SKILLS_DIR", raising=False)
    return root


def test_available_skills_lists_fixture_tree(skill_root: Path):
    assert set(loader.get_available_skills()) == {
        "lark-contact",
        "lark-shared",
        "lark-whiteboard",
        "lark-slides",
    }


def test_default_path_returns_top_level_skill_md_with_banner(skill_root: Path):
    body = loader.load_skill_file("lark-contact")
    assert body is not None
    assert body.startswith("> ⚠️"), "banner must be the first line"
    # frontmatter stripped
    assert "name: lark-contact" not in body
    # markdown header preserved
    assert "# contact" in body


def test_internal_reference_link_rewritten_to_mcp_call(skill_root: Path):
    body = loader.load_skill_file("lark-contact")
    assert body is not None
    # The original target stays (so markdown renders) but the label now
    # carries the explicit MCP call hint.
    assert (
        'lark_skill(agent_id, "lark-contact", '
        'path="references/lark-contact-search-user.md")'
    ) in body
    # Still referenced by both +search-user and +get-user
    assert body.count("references/lark-contact-get-user.md") >= 2


def test_cross_skill_link_rewritten_with_parent_traversal(skill_root: Path):
    body = loader.load_skill_file("lark-contact")
    assert body is not None
    assert (
        'lark_skill(agent_id, "lark-shared", path="SKILL.md")'
    ) in body


def test_non_references_subdirs_rewritten(skill_root: Path):
    body = loader.load_skill_file("lark-whiteboard")
    assert body is not None
    assert (
        'lark_skill(agent_id, "lark-whiteboard", path="routes/mermaid.md")'
    ) in body
    assert (
        'lark_skill(agent_id, "lark-whiteboard", path="scenes/funnel.md")'
    ) in body


def test_nested_reference_file_also_gets_banner_and_rewrite(skill_root: Path):
    body = loader.load_skill_file(
        "lark-contact", path="references/lark-contact-search-user.md"
    )
    assert body is not None
    assert body.startswith("> ⚠️")
    # Within-references cross-link — should rewrite relative to the
    # current skill directory (lark-contact), not relative to references/.
    # Input: [get-user](lark-contact-get-user.md) → path is bare filename,
    # which resolves under skill_dir/lark-contact-get-user.md (doesn't
    # exist — that's fine, the rewrite is about teaching the Agent the
    # call shape, not pre-validating existence).
    assert 'lark_skill(agent_id, "lark-contact", path=' in body


def test_non_markdown_file_returned_verbatim(skill_root: Path):
    body = loader.load_skill_file(
        "lark-slides", path="references/slides_schema.xml"
    )
    assert body is not None
    assert body.startswith("<?xml")
    assert "> ⚠️" not in body, "XML must not get the markdown banner"


def test_missing_file_returns_none(skill_root: Path):
    assert loader.load_skill_file("lark-contact", path="nope.md") is None


def test_missing_skill_returns_none(skill_root: Path):
    assert loader.load_skill_file("lark-does-not-exist") is None


def test_path_traversal_escape_rejected(skill_root: Path):
    # Attempt to climb out of lark-contact into its sibling.
    assert (
        loader.load_skill_file("lark-contact", path="../lark-shared/SKILL.md")
        is None
    )
    # Classic absolute-path poke.
    assert loader.load_skill_file("lark-contact", path="/etc/passwd") is None


def test_absolute_url_links_not_rewritten(skill_root: Path, tmp_path: Path):
    # Safety check: external URLs must not be mangled.
    contact = skill_root / "lark-contact"
    (contact / "SKILL.md").write_text(
        "# contact\n\nSee [Lark docs](https://open.larksuite.com/doc).\n",
        encoding="utf-8",
    )
    body = loader.load_skill_file("lark-contact")
    assert body is not None
    assert "https://open.larksuite.com/doc" in body
    # rewrite marker MUST NOT appear next to a URL
    lines = [ln for ln in body.splitlines() if "larksuite.com" in ln]
    assert lines and "call `lark_skill(" not in lines[0]
