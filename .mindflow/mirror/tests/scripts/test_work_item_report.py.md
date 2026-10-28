---
code_file: tests/scripts/test_work_item_report.py
last_verified: 2026-08-17
stub: false
---

## 2026-08-17 — 为什么存在

守 `work_item_report` 的算术。这份报告的用途只有一个：给 PR #230「上更强兜底前
先测量」提供那个数字。所以**报告说谎比报告缺席更糟**——决策者会照着一个错的闭环
率决定「不需要兜底」。

本文件钉的三个坑都属于「悄悄说谎」而不是「明确报错」：

- **比率不得超过 100%**：窗口内闭环、窗口外开的项没有 open 行可配对，算进分子
  就会让分子大过分母。
- **一次断链只算一次 stall**：约束其实在**生产端**（[[patrol]] 只在状态迁移时
  打日志），这里的测试是它回退时的回归网。
- **默认不混 origin**：`tool` 是任务、`auto` 是差事，混起来「闭环率」同时表示两
  件事。

另有本地时 + `.zip` 轮转档两条，与 [[latency_report]] 同款——两个坑都以「没有数
据」的形式失败，而那读起来像「功能没在记录」。

## 关联

被测：`scripts/diag_collector/work_item_report.py`。
数据来源：[[errand]]（open/close/expire）、[[patrol]]（stall）。
