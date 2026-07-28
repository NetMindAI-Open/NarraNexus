---
code_file: frontend/src/lib/agentLimits.ts
last_verified: 2026-07-23
stub: false
---

# agentLimits.ts — 前端 agent 字段长度上限

单一常量 `AGENT_TEXT_MAX_LENGTH = 255`,镜像后端
`src/xyz_agent_context/schema/entity_schema.py` 的同名常量。`agent_name` /
`agent_description` 在写边界(后端超限返回 422)、bundle 导入(截断到此长度)、
以及前端([[EditAgentDialog.tsx]] 计数 + 禁用、[[AgentGroupSection.tsx]] inline
改名的 maxLength)都用这一个上限。

## Gotcha

这是**手工镜像**——前后端没有共享类型生成(见 [[api_schema.py]] "为什么不自动
生成 TS 类型")。改后端常量时必须同步改这里,否则前端会在一个和服务端不一致的
长度上放行/拦截。
