---
code_file: tests/nexus_power/test_event_adapter.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（B-05/#127）— TYPE_ERROR 的 `fatal` 透传

`fatal` 是框架自报的（终局 **且** 本 turn 未交付任何输出）：payload 带 `fatal: False` 时翻译结果
必须仍是 `False`（不被改回 `True`）；payload 不带这个键时取保守默认 `True`。

# tests/event_adapter — monologue 标志映射

text_delta→thinking_item 必须带 monologue:true(平台 reasoning 链靠它接通);
thinking_delta(CoT)必须不带。防止两者混淆导致 CoT 进 final_output 或独白丢失。
