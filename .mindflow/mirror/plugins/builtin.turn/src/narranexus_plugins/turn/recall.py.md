---
code_file: plugins/builtin.turn/src/narranexus_plugins/turn/recall.py
last_verified: 2026-09-04
stub: false
---

## 2026-09-04（批 3a）— Recall 阶段策略

`default` = step_1 LLM 选叙事 + step_1_5 读历史；`narrative_fast`/`ephemeral` = step_1_fast_select（BM25 top-1）
+ 不读历史；二者差别（miss 时是否建叙事/写 session）由 profile 的 TurnProfile.narrative_persistence 决定，
step 从 `ctx.turn_profile` 读。

步骤函数经 `agent_runtime` 模块属性调用（`ar.step_*`），既有测试 monkeypatch `agent_runtime.step_*` 的接缝继续有效。
