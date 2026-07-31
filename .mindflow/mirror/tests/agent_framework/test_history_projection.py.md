---
code_file: tests/agent_framework/test_history_projection.py
last_verified: 2026-07-29
stub: false
---
# tests/history_projection — 折叠不变量

provider 合法性(悬空 call 批边界合成/孤儿丢弃/并行按 id 配对/无 id 按最老兜底)、
EventLogEntry 包装解包、坏行不抛、纯 CoT turn 折空(claude log 形状)。
