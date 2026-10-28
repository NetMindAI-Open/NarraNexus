---
code_file: plugins/builtin.turn/src/narranexus_plugins/turn/assemble.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3a）— Assemble 阶段策略

`layered_prompt`：只在 Act 将走 agent loop 时运行（direct-trigger / silent 不建提示，遗留行为），调
`step_3_assemble_context`（原 3.1–3.3），并把 `agent.capabilities.context_providers` 里、profile 能力过滤放行的
提供者交给 ContextRuntime 追加段落。

步骤函数经 `agent_runtime` 模块属性调用（`ar.step_*`），既有测试 monkeypatch `agent_runtime.step_*` 的接缝继续有效。
