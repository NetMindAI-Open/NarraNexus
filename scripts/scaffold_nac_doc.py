"""
@file_name: scaffold_nac_doc.py
@author: NexusAgent
@date: 2026-04-09
@description: Generate stub mirror mds for every code file / directory in scope.

Phase 1 of the NAC Doc seeding strategy. Idempotent: never overwrites existing
mds. Stubs use the §5.3 / §5.4 templates from the design spec with all content
sections filled with `<!-- TODO: intent -->` placeholders.

Run from repo root:
    uv run python -m scripts.scaffold_nac_doc
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from scripts import nac_doc_lib


FILE_STUB_TEMPLATE = """\
---
code_file: {rel}
last_verified: {today}
stub: true
---

# {name} — <!-- TODO: one-line role -->

## 为什么存在
<!-- TODO: intent -->

## 上下游关系
- **被谁用**：<!-- TODO: intent -->
- **依赖谁**：<!-- TODO: intent -->

## 设计决策
<!-- TODO: intent -->

## Gotcha / 边界情况
<!-- TODO: intent -->

## 新人易踩的坑
<!-- TODO: intent -->
"""


OVERVIEW_STUB_TEMPLATE = """\
---
code_dir: {rel}
last_verified: {today}
stub: true
---

# {name}/ — <!-- TODO: one-line role -->

## 目录角色
<!-- TODO: intent -->

## 关键文件索引
<!-- TODO: intent -->

## 和外部目录的协作
<!-- TODO: intent -->
"""


def scaffold() -> None:
    """Walk source trees and write stub mds for anything missing."""
    plan = nac_doc_lib.walk_source_trees()
    today = date.today().isoformat()
    root = nac_doc_lib.repo_root()

    created = 0
    for code_file in plan.required_file_mds:
        md_path = nac_doc_lib.mirror_path_for_code_file(code_file)
        if md_path.exists():
            continue
        md_path.parent.mkdir(parents=True, exist_ok=True)
        rel = code_file.relative_to(root).as_posix()
        md_path.write_text(
            FILE_STUB_TEMPLATE.format(rel=rel, today=today, name=code_file.name),
            encoding="utf-8",
        )
        created += 1

    for code_dir in plan.required_dir_overviews:
        md_path = nac_doc_lib.mirror_path_for_dir(code_dir)
        if md_path.exists():
            continue
        md_path.parent.mkdir(parents=True, exist_ok=True)
        rel = code_dir.relative_to(root).as_posix() + "/"
        md_path.write_text(
            OVERVIEW_STUB_TEMPLATE.format(rel=rel, today=today, name=code_dir.name),
            encoding="utf-8",
        )
        created += 1

    print(f"[scaffold_nac_doc] Created {created} new stub md files.")


if __name__ == "__main__":
    scaffold()
