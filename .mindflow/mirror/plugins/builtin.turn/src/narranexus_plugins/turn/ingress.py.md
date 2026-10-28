---
code_file: plugins/builtin.turn/src/narranexus_plugins/turn/ingress.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3a）— Ingress 阶段策略

step_0（配置/Event/Session）+ 本回合 owner 的 LLM 配置解析，逐字搬自 `run()`。解析失败：错误标记落
event、yield `ErrorMessage`、置 `services.aborted`（= 原来的提前 return）。`test_llm_resolver_log_level`
改为白盒检查这里（不许 `logger.exception`）。

步骤函数经 `agent_runtime` 模块属性调用（`ar.step_*`），既有测试 monkeypatch `agent_runtime.step_*` 的接缝继续有效。
