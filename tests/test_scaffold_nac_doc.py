"""
@file_name: test_scaffold_nac_doc.py
@author: NexusAgent
@date: 2026-04-09
@description: Integration tests for scripts/scaffold_nac_doc.py.

Uses a synthetic repo layout inside tmp_path and monkeypatches nac_doc_lib's
INCLUDE_SPECS + repo_root so the scaffold script operates on the fake tree.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import nac_doc_lib
from scripts.scaffold_nac_doc import scaffold


@pytest.fixture
def fake_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """
    Build a fake repo tree with one mirrored source root and a few files,
    and point nac_doc_lib at it.
    """
    src = tmp_path / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("", encoding="utf-8")  # pure re-export → skipped
    (src / "real.py").write_text("def foo(): pass\n", encoding="utf-8")  # included
    (src / "utils").mkdir()
    (src / "utils" / "helper.py").write_text("x = 1\n", encoding="utf-8")
    (src / "_impl_stuff").mkdir()  # does NOT match _.*_impl pattern — it's a normal dir
    (src / "_impl_stuff" / "guts.py").write_text("y = 2\n", encoding="utf-8")
    (src / "_module_impl").mkdir()  # matches overview-only pattern
    (src / "_module_impl" / "hidden.py").write_text("z = 3\n", encoding="utf-8")
    (src / "__pycache__").mkdir()
    (src / "__pycache__" / "x.pyc").write_text("", encoding="utf-8")

    (tmp_path / ".nac_doc" / "mirror").mkdir(parents=True)

    monkeypatch.setattr(nac_doc_lib, "INCLUDE_SPECS", (
        nac_doc_lib.IncludeSpec(root="src/pkg", extensions=(".py",)),
    ))
    monkeypatch.setattr(nac_doc_lib, "repo_root", lambda: tmp_path)
    monkeypatch.setattr(nac_doc_lib, "mirror_root", lambda: tmp_path / ".nac_doc" / "mirror")
    return tmp_path


def test_scaffold_creates_mirror_md_for_real_files(fake_repo: Path) -> None:
    scaffold()

    mirror = fake_repo / ".nac_doc" / "mirror"
    # real.py gets a mirror md
    assert (mirror / "src/pkg/real.py.md").exists()
    # helper.py in utils/ gets a mirror md
    assert (mirror / "src/pkg/utils/helper.py.md").exists()
    # guts.py in _impl_stuff/ gets one (does NOT match _.*_impl)
    assert (mirror / "src/pkg/_impl_stuff/guts.py.md").exists()


def test_scaffold_skips_pure_reexport_init(fake_repo: Path) -> None:
    scaffold()
    mirror = fake_repo / ".nac_doc" / "mirror"
    assert not (mirror / "src/pkg/__init__.py.md").exists()


def test_scaffold_skips_overview_only_dir_internals(fake_repo: Path) -> None:
    scaffold()
    mirror = fake_repo / ".nac_doc" / "mirror"
    # _module_impl/hidden.py MUST NOT have its own md
    assert not (mirror / "src/pkg/_module_impl/hidden.py.md").exists()
    # But _module_impl/_overview.md MUST exist
    assert (mirror / "src/pkg/_module_impl/_overview.md").exists()


def test_scaffold_creates_overview_for_every_dir(fake_repo: Path) -> None:
    scaffold()
    mirror = fake_repo / ".nac_doc" / "mirror"
    assert (mirror / "src/pkg/_overview.md").exists()
    assert (mirror / "src/pkg/utils/_overview.md").exists()
    assert (mirror / "src/pkg/_impl_stuff/_overview.md").exists()


def test_scaffold_skips_pycache(fake_repo: Path) -> None:
    scaffold()
    mirror = fake_repo / ".nac_doc" / "mirror"
    assert not (mirror / "src/pkg/__pycache__").exists()


def test_scaffold_stub_has_frontmatter_and_todo(fake_repo: Path) -> None:
    scaffold()
    md = (fake_repo / ".nac_doc/mirror/src/pkg/real.py.md").read_text(encoding="utf-8")
    assert md.startswith("---")
    assert "code_file: src/pkg/real.py" in md
    assert "stub: true" in md
    assert "last_verified:" in md
    assert "<!-- TODO: intent -->" in md


def test_scaffold_overview_stub_has_code_dir(fake_repo: Path) -> None:
    scaffold()
    md = (fake_repo / ".nac_doc/mirror/src/pkg/utils/_overview.md").read_text(encoding="utf-8")
    assert "code_dir: src/pkg/utils/" in md
    assert "stub: true" in md


def test_scaffold_is_idempotent_does_not_overwrite(fake_repo: Path) -> None:
    scaffold()
    md_path = fake_repo / ".nac_doc/mirror/src/pkg/real.py.md"
    # Simulate human edit: replace stub content
    md_path.write_text(
        "---\ncode_file: src/pkg/real.py\nlast_verified: 2026-04-09\nstub: false\n---\n"
        "\n# real.py — edited by human\n",
        encoding="utf-8",
    )
    scaffold()  # run again
    content = md_path.read_text(encoding="utf-8")
    assert "edited by human" in content
    assert "stub: false" in content
