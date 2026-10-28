---
code_file: plugins/builtin.turn/src/narranexus_plugins/turn/act.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3a）— Act 阶段策略

`default`：`step_3_execute_path` + 中断排水，取消则标 interrupted 并保留部分结果（逐字搬）；`silent`：写空
`PathExecutionResult`，不叫模型。

步骤函数经 `agent_runtime` 模块属性调用（`ar.step_*`），既有测试 monkeypatch `agent_runtime.step_*` 的接缝继续有效。
