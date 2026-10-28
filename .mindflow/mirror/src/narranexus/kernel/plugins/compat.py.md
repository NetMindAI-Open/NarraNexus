---
code_file: src/narranexus/kernel/plugins/compat.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2b.5）— `host_version` / `select_version` / `blocked_reason`

宿主版本取包元数据（`narranexus` 或 `xyz-agent-context`），拿不到给 `0.0.0`；`select_version` 是 Obsidian
`versions.json` 规则（老宿主装满足条件的最新旧版而不是拒绝）；`blocked_reason` 是官方索引
`blocked_versions.json`（`{id: {below, reason}}`）的命中判定。

## 2026-09-03 — 自写的最小 semver（不引 `packaging`）

manifest 的 `version`/`minAppVersion`/`dependencies`/`api` 都要比较版本。只实现插件作者真会写的
子集：精确、`>= <` 比较子（空格分隔取交）、`^`（0.x 特例按 npm 规则）、`~`、`*`；预发布标签
排在正式版之前，build 元数据忽略。主版本单独写（`<2`）也合法（`Range(">=1.19 <2")` 就靠它）。
不引 `packaging` 的原因：内核 import 要轻，且 PEP 440 与 semver 的预发布语法不同，插件生态
（Obsidian/VS Code）都是 semver。
