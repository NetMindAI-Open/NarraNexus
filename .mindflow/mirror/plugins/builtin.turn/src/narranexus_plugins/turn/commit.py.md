---
code_file: plugins/builtin.turn/src/narranexus_plugins/turn/commit.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3a）— Commit 阶段策略

step_4 持久化 + `persist_turn` + `[turn-timing]` 行（时间点来自 `services.timings`）+ 取消检查。

步骤函数经 `agent_runtime` 模块属性调用（`ar.step_*`），既有测试 monkeypatch `agent_runtime.step_*` 的接缝继续有效。
