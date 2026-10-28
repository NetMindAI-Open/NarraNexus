---
code_file: tests/nexus_power/test_error_classifier.py
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 浏览器视觉输入

钉住各家「不支持图片」措辞归为 IMAGE_INPUT_REJECTED（不可重试、legacy 映射 invalid_request），
以及含 image 字样的无关 400 不被误判。

# tests/error_classifier — 分类表与链遍历

overflow 优先、类名>消息、未知保守、遗留映射不漏新词汇、重试策略。
