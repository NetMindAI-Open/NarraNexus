---
code_file: tests/nexus_power/test_modeling.py
last_verified: 2026-07-31
stub: false
---
# tests/modeling — 方言解析/断点/chunk 翻译/裁剪

假 chunk 流验证事件切分与 usage 双词汇换算;路由前缀按协议且无条件前置(平台 id 可自带路由名,豁免=裸名 404);裁剪保尾。

## 2026-07-31 — 本文件现在承载「输出上限的事实源」这条不变量

除方言解析/断点/chunk 翻译/裁剪外,这里还钉住:上限与窗口来自
`providers/model_catalog`(不是框架内本地表)、协议永远不授予厂商上限、
**抬高上限需同时有实测 window 而压低不需要**、以及钳制(`output_budget`)的边界行为。
这批断言含具体数字(115_200 / 57_600 / 8_192),catalog 改数时会一起红——那是有意的,
它们就是防止两处数字悄悄分叉的哨兵。
