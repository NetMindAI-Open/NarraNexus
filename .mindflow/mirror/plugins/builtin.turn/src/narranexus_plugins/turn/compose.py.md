---
code_file: plugins/builtin.turn/src/narranexus_plugins/turn/compose.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3a）— Compose 阶段策略

step_2（模块加载 + 执行路径决策）+ step_2_5（实例同步），两处取消检查照旧。

步骤函数经 `agent_runtime` 模块属性调用（`ar.step_*`），既有测试 monkeypatch `agent_runtime.step_*` 的接缝继续有效。
