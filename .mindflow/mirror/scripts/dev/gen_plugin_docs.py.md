---
code_file: scripts/dev/gen_plugin_docs.py
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 ui.toolRenderers（第 18 个注册表）

host-api 内容注册表一行加入 `toolRenderers`；重新生成 slots/contributes/host-api 三份文档。

## 2026-09-03 — 从代码生成 `docs/plugins/slots.md`

插件作者的参考文档（契约版本、扩展位树、内置插件清单）由 `narranexus.contracts.API_VERSIONS/
STABILITY`、`build_kernel_slot_tree().to_rows()`、`builtin_manifests()` 渲染，不手写——
spec §13「文档不落后」。`tests/nx_kernel/test_docs_generated.py` 断言提交的文件与 `render()` 逐字
相等，改了扩展位或版本却没跑 `--write` 会红。放在 `scripts/dev/` 与 `narranexus_migrate.py` 同组。

## 2026-09-04 · slot points in the Host API table

The hand-written `HOST_API_TABLE` lists the content registries and the seven slot points with the `when` grammar.

2026-09-07: slots.md groups the slot table by domain (catalog.DOMAINS order).

## 2026-09-07 — 从带 builtin 声明的树生成（B7）

slots.md/contributes.md 改用 slot_tree_with_builtins()（内核树已不含阶段/prompt/ui 子槽）、catalog.domains() 排序标题、row['kind'] 取种类。
