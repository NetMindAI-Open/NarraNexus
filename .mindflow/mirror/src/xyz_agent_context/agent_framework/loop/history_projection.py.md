---
code_file: src/xyz_agent_context/agent_framework/loop/history_projection.py
last_verified: 2026-07-29
stub: false
---
# history_projection — event_log → provider 消息的原生回放折叠

跨 turn 历史保真的读侧(写侧早已存在:step_4 把 all_steps 原样存 events.event_log,
驱动中立)。把一个历史 turn 的 steps 折回 assistant(text+tool_calls)/tool 消息序列,
供自投影框架(NexusPower)把真实对话结构带进下一轮,替代两行拍平摘要。

不可妥协的不变量:输出必须 provider 合法——每个 assistant tool_calls 在下一条
assistant 消息前配齐 tool 消息(悬空 call 合成占位结果;孤儿 output 丢弃;无 id 的
output 按最老未答配对兜底)。逐行 fail-open:坏行降级回放,绝不炸 turn。

为什么只服务 NexusPower(Owner 拍板 Q1):claude/codex 的 assistant text 走
append_text 进 final_output,从不进 all_steps——它们的 log 折出来只有工具流没有
文字;只有 nexus turn 带定位的 monologue 段(record_thinking 存进 step dict)。
