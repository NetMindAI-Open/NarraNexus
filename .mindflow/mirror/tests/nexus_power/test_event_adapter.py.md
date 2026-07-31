---
code_file: tests/nexus_power/test_event_adapter.py
last_verified: 2026-07-29
stub: false
---
# tests/event_adapter — monologue 标志映射

text_delta→thinking_item 必须带 monologue:true(平台 reasoning 链靠它接通);
thinking_delta(CoT)必须不带。防止两者混淆导致 CoT 进 final_output 或独白丢失。
