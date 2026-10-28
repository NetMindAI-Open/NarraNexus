---
code_file: tests/nexus_power/test_loop_e2e.py
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 浏览器视觉输入

撤图修复三条：拒绝后本轮后续请求都不带图、每张图位置换成说明、工具文本保留、本轮正常结束；
请求里没图时同样的报错直接失败不重试；修复只武装一次。

## 2026-09-10（B-05/#127）— `_fail` 的 `fatal` 正负例

已通过表达工具答过话后再 `_fail` → payload `fatal is False`；本轮从未表达过 → `fatal is True`。
无表达工具的 plain-text turn：流出过文本后失败 → `False`，没流出文本 → `True`；有表达工具时 monologue 文本
不算交付 → `True`。只在中途断掉的 attempt 里流出、被 `discard_step()` 丢弃的文本不算交付 → `True`；
提交时尚无表达工具的文本、之后 `expand()` 中途授予表达工具再失败 → 仍 `False`；授予之后才写的文本 → `True`
（`FakeModel` 的 step 列表可在事件中间放 Exception，模拟流出前缀后断流；`_GrantingTools` 执行 `expand` 时调
`add_tools`）。截断失败经 event_adapter 到平台的 error_type 是 `output_budget_exhausted`（普通 provider 错误不是），
文案报实际发出的 `max_tokens`。

## 2026-09-10（B-03）— 空产出 + `max_tokens` 的截断重试

零文本零工具调用 + `stop_reason=max_tokens`：翻倍地板乘数重放一次成功 → NO_MORE_ACTIONS 无
TYPE_ERROR，且请求乘数序列为 `[1, 2, 1]`（重放那一步之后复位）、共享 `params.extra` 从未被写；
翻倍后仍空 → `OUTPUT_TRUNCATED` 真失败（恰一次重试）；真实 DeepSeek-V4-Pro profile（ceiling==地板）
首次截断即失败不重放；用户钉了 `max_tokens` → 不重放且失败文案报钉住的值；重放不拼接被丢弃的 CoT；
负例——有文本的 `max_tokens` 收尾不算截断。

# tests/loop_e2e — 脚本化假模型端到端

工具往返终止/标签工具参数流出=回复/取消合成配对且恰一次收口/溢出压缩重试/不可重试错误→error+done/遗留六形状金样/插话经真 loop 进下次请求(+无插话一步即停对照)。
