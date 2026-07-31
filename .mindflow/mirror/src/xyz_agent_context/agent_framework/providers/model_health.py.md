---
code_file: src/xyz_agent_context/agent_framework/providers/model_health.py
last_verified: 2026-07-30
stub: false
---

# agent_framework/providers/model_health.py — 运行时模型失败反哺探测

## Why it exists

探测 ledger 只在模型从上游 catalog 消失时才清理；一个"还挂在 catalog 页但推理
后端已死"的模型会留在所有用户下拉框里，直到有人真调它撞上报错——而这个报错
以前不回流到任何地方。本模块把 live 失败变成探测信号，闭合回路。

## How it works / design

- 入口是 step_3 的错误归因（[[failure]]`.classify_self_serviceable` →
  `model_not_found`，余额/限流/5xx 不会归到这里），不是新的文本解析。
- `report_agent_slot_suspect` 在报错时刻把 agent slot 绑定解析回
  (provider source, protocol, model)（agent_slots 覆盖 user_slots，镜像
  resolver 的 overlay 顺序），写入 `model_probe_suspects` 表。
- **嫌疑只加速复测，不直接摘除**：[[model_sync]] 把 suspect 视为立即过期、
  优先于 TTL 队列，probe verdict 仍是唯一裁决。误报的代价 = 一次探测调用。
- 只记录可探测 source（netmind/openrouter/yunwu；system_pool 归一到 netmind）。
  OAuth CLI / custom / 免费网关卡没有探测路径，记了就是死数据，直接返回 False。
- 全程 best-effort：反馈路径永不反噬错误路径本身。

## Upstream / downstream

- 写方：`step_3_agent_loop` 的 fallback-skip 判定后钩子。
- 读方：[[model_sync_runner]] 与 backend `sync-defaults` 路由——加载嫌疑传入
  `sync_source(suspects=)`，pass 结束后 `clear_suspects`。
