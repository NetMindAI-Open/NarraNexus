---
code_file: src/xyz_agent_context/agent_framework/llm/_prompt_probe.py
last_verified: 2026-07-30
stub: false
---

# _prompt_probe.py — helper prompt 形状诊断（默认关闭）

## 为什么存在

2026-07-29 实测 helper 每次 18,288 输入 token、每轮约 6 次。所有修法
（开缓存 / 精简上下文 / 减少次数）都取决于一个没人查过的事实：**那 18K 里
装的是什么，以及跨调用有多少是相同的。**

两个发现让「照直觉修」不安全：
- `instance_decision` 的 `instructions` 是 **85 字符**常量，18K 全在
  `user_input`。而 SDK 把 instructions 映射到 `system`、user_input 映射到消息，
  所以「给 system 加 cache_control」只会缓存 85 个字符。
- 每轮 6 次是 **6 个不同的 helper**，前缀互不相同 —— 不等于 6 次复用同一份缓存。

加上一条硬约束：`claude-haiku-4-5` 的 `prompt_cache_min_tokens = 4096`。
短于此的前缀根本不可缓存。所以问题不是「有没有重复」而是
**「有没有 ≥4096 token 逐字节相同的开头，且在 5 分钟 TTL 内重复」** —— 可测量。

## 它发射什么

每次调用一行 `[HELPER-PROMPT]`，**不含任何 prompt 内容**：长度 + `user_input`
在递增字节边界处的**前缀切片**哈希。

用前缀切片而不是整体哈希，是因为缓存**从第 0 字节开始匹配**：整体哈希只能区分
「完全相同 / 完全不同」，分不出「共享一段长前缀」。比较同一调用点的两次调用，
最大的仍匹配的边界就框出了共享前缀的长度。

`HELPER_PROMPT_DUMP_DIR` 是**独立的第二个开关**，会把完整 payload 落盘（把区间
升级成精确 LCP）—— 那会把对话内容写到磁盘，所以只应指向临时目录。

两个开关都默认关闭。这条代码在每次 helper 调用的热路径上，所以开关检查发生在
任何哈希计算和栈回溯**之前**。

## 用 `sys._getframe` 而非 `inspect.stack`

后者会为每一层构造完整 FrameInfo（含源码查找），在一个每轮跑 7 次的路径上贵一个
数量级。回溯上限 12 层，避免异常深的栈把诊断变成长时间遍历。

## 实测结果（2026-07-30）

我们的 prompt 波动 8.6×（801–6,903 字符），账本 total 只波动 1.09×
（17,574–19,242 token）→ **每次约 16,000 token 与我们发的内容无关**，
是 Claude Code CLI 自己注入的系统提示词。结论见
`reference/self_notebook/2026-07-30-helper-token-investigation-findings.md`。

相关：[[cli_helper]]、[[anthropic_helper]]、[[model_pricing]]。
