---
code_file: packages/narranexus-contracts/src/narranexus/contracts/agent/capability.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07（批 1 四轮复审移植）— `StageParticipant.tools` 改 `async`

与其它格子一致：参与方可能要先问后端服务（MCP server 配置）才知道自己的工具。本分支没有实现者（`LegacyModuleAdapter`
已在批 5 删除），契约先改，Act 阶段调用方一律 `await`。

## 2026-09-07（批 1 三轮复审移植）— tier 表的口径

批 1 复审指出 TOOL 档 `{ACT}` 与「工具面只能经 Assemble 的 `contribute_tools` 产出」矛盾。本分支的模型已经不同：
工具插件走 `contracts.tool.ToolProvider.list_tools`（Act），MCP server 经 `agent.capabilities.mcp_servers` 位声明，
所以 TOOL = Act-only 在这里是自洽的，表保持不变，docstring 把这条依据写明。另外去掉了「runtime rejects it」——
今天没有任何校验器按 `TIER_STAGES` 拒绝能力（`participations()` 只从它派生），表是阶段级上界的文档。

## 2026-09-04（批 3b）— `ContextProvider` 加 `@runtime_checkable`（Assemble 用 isinstance 筛提供者）

## 2026-09-04（批 3a）— `ContextProvider` Protocol

`agent.capabilities.context_providers` 位的契约：`name`、`context_cost_hint`、可选 `contribute_instructions`/
`contribute_turn_context`。

## 2026-09-03 — 能力 = 阶段参与集合 + 元数据（五级一个契约）

`CapabilityTier` 五级不是五套机制，而是同一个 `Capability` 契约填了多少格子，`TIER_STAGES` 是这张表：
Tool 只在 Act（`tools`）、ContextProvider 只在 Assemble、Skill 在 Assemble+Act、MemoryKind 在
Recall（`recall`）+Commit+Reflect、Module 除 Compose 外全部（Compose 是平台自己的阶段，没有参与方法）。
`StageParticipant` 的方法全部可选（结构化 Protocol），`STAGE_METHODS` 是「哪个方法属于哪个阶段」的
唯一真源，六个有参与方法的阶段各有一行；运行时按 `STAGE_METHODS` 调用参与方法；批 5c 起 `XYZBaseModule` 原生实现该契约（九个生命周期方法即阶段名），适配器已删。
Protocol 属性集相等，且每个 tier 允许的阶段都能用 `STAGE_METHODS` 表达）。预审曾指出 docstring
写「四级」而枚举有五个、Recall/Act 无行——已改正。
`CapabilityMeta` 收拢了研究文档 H1 里散在 7 张表的字段（priority/always_load/is_task/
provides_chat_history/instance_prefix/context_cost_hint）。`XYZBaseModule` 九方法的映射写在
模块 docstring。

## 2026-09-04 · no adapter (batch 5c)

`XYZBaseModule` implements this contract natively; the docstring mapping now reads as the module's own method names.
