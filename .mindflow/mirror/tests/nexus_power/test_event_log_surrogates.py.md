---
code_file: tests/nexus_power/test_event_log_surrogates.py
last_verified: 2026-09-11
stub: false
---
# tests/event_log_surrogates — 事件日志遇 surrogate 不抛错

`ndjson_line` 对含代理对/孤立 surrogate 的行输出严格可 UTF-8 编码的文本（对→emoji，孤立→U+FFFD），非 ASCII 原样保留；
`FileEventLogWriter` 追加含孤立 surrogate 的事件不抛错且可读回。回退 scrub 时变红（prod 2026-09-11 事故回归）。
