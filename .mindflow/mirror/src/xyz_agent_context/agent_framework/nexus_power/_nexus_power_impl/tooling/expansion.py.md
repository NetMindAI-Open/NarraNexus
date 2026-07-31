---
code_file: src/xyz_agent_context/agent_framework/nexus_power/_nexus_power_impl/tooling/expansion.py
last_verified: 2026-07-31
stub: false
---

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

`Expandable` 新增 `expressive_tools`(能力包携带的投递工具,全限定名);
`CapabilityExpander` 新增 `add_expressive` 缝,expand 时把声明并进
ExpressionContract(幂等随 _expanded)。为 module→expandable 接线预埋:
channel 能力中途展开时其回复工具立即被表达契约认账。

# tooling/expansion — Expandable/CapabilityExpander(框架中性)

框架不认识 module:Expandable=指令/MCP/技能目录/env 全可选的一包能力,平台自行翻译传入。回合内有效;expand_initial 起跑展开进稳定前缀(cache 友好、未知 key fail-fast=接线 bug),运行中展开经 tool_result 尾部追加;跨回合记性=平台消费日志展开事件。
