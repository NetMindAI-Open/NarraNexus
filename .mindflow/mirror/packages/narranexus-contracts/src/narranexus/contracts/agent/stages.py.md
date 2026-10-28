---
code_file: packages/narranexus-contracts/src/narranexus/contracts/agent/stages.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03 — 七阶段与阶段间的 frozen 值对象

`Stage` 顺序固定（Ingress→Recall→Compose→Assemble→Act→Commit→Reflect），一比一对应现有
step_0 / step_1+1.5 / step_2+2.5 / context_runtime+模块 get_* / step_3 / step_4+persist_turn /
step_5。七个 `*Context` 只收「跨阶段边界」的字段（从平台私有的 `RunContext` 里挑），
`AssembleContext` 用 sha256+长度而不是整段提示，保持值小且字节稳定（approval 快照切点）。
`Budgets` 只约束插件参与（同步 hook 超时/总预算、上下文 token 提示、成本），绝不给 agent loop
本身设上限（铁律 #14）。
