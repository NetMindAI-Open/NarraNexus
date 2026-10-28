---
code_file: packages/narranexus-contracts/src/narranexus/contracts/agent/__init__.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07（批 1 三轮复审移植）— 门面补齐三张表；三个 `events` 的分工

`STAGE_METHODS` / `TIER_STAGES` / `BUILTIN_PROFILE_IDS` 进门面 `__all__`（此前只导出 `STAGE_HOOKS`，消费方要记
哪张表在门面、哪张要下钻）。包 docstring 写明三个同名模块的分工：`contracts.agent.events` = 阶段 hook 名；
`contracts.agent_events` = agent loop 的线协议事件字典；`contracts.events` = 宿主事件总线名。不改文件名：
`agent_events` 已进 `API_VERSIONS` key、golden 文件名与全部 importer。

## 2026-09-03 — Agent 纵横模型的契约包（spec §7，D23）

纵轴 = 七阶段回合流水线（`stages`），横轴 = 能力（`capability`），每阶段策略由 `pipeline` 的
`PipelineProfile` 选择，整个 Agent 是 `agent_spec.AgentSpec` 一个值对象，阶段观察钩子在 `events`。
全部是值类型或结构化 Protocol：平台实现阶段，插件实现能力与策略，双方都不 import 对方。
批 1 只立契约与过渡期适配器（批 5c 起模块原生实现契约，适配器已删）；`platform/turn/` 的阶段编排器与
`stageStrategies/pipelineProfiles/contextProviders` 三 kind 是批 3。契约版本 `API_VERSIONS["agent"]`。公开面额外带 `TurnPipeline`/`ActStrategy`（扩展位契约符号）
与 `ToolSurfaceView`（Assemble 输出里的工具面视图）。
