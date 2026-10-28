---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/_nexus_power_impl/tooling/policy.py
last_verified: 2026-09-07
stub: false
---

## 2026-08-10 (review 修正) — 字段改名，并补两条守卫

`extra_readable_roots` → `extra_accessible_roots`。名字必须诚实：`_PATH_ARG_NAMES` 含
`file_path`，`ShellConfinementLayer` 管 shell 路径，所以这份授予同时管**写和删**，
不是只读。

`tests/nexus_power/test_tooling.py` 补了两条：**兄弟 team 目录仍被拒**（证明比较的是被授予
路径本身而非其父目录——共同父目录恰好是 `_shared`，装着该 owner 的所有 team），以及
**授予覆盖写**（把名字的含义钉死）。

写第一条时踩到一个陷阱值得记：把 `_shared` 建在 workspace **内部**会让测试凭 workspace 包含
关系通过，**假绿**、什么也没证明。真实布局里 `_shared` 是 workspace 的 sibling，测试必须照此
搭建。

## 2026-08-07 — extra_readable_roots：协作区放行（团队共享工作台前置）

confinement 从「只认 workspace」改为「workspace **+ 平台声明的额外根**」（`_permitted_roots`
/ `_within_permitted`，两个 layer 共用）。动机是一个当时就存在的自相矛盾：team prompt
（[[message_bus_trigger.py]]）明写让 agent 用 Read 打开 `_shared/teams/{id}`，而该目录
按设计是 agent workspace 的 **sibling**（谁都不拥有它）→ 必然越界 → DENY；claude/codex
没有这层，三框架两种行为。

fail-closed 未被削弱：框架**从不自行推导**根目录——推导 `workspace.parent/_shared` 在
旧 flat 布局（`{agent_id}_{user_id}`）下会解析成整个 base，放行面直接失控。根由知道
user 的调用方显式传入，解析失败的根被丢弃而非放宽。相对路径仍只相对 workspace 解析，
额外根只能用绝对路径抵达。空元组（缺省）= 逐字复现旧行为。

# tooling/policy — fail-closed 策略引擎

有序 layer、deny 永远赢、layer 崩溃即拒(多租户自研决策,业界无背书,Codex fail-open 是反面)。WorkspaceConfinement 只查内建工具路径参数(mcp__ 的副作用在服务端),拒绝并点名路径,不静默改写。

## 2026-09-07 — an empty PolicyEngine denies

Zero layers is a misassembly, not permission: check() answers DENY (fail-closed) instead of ALLOW; layers is exposed for tests.
