---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/contracts/errors.py
last_verified: 2026-09-24
stub: false
---

## 2026-09-24 浏览器视觉输入

新增第四个信号 `IMAGE_INPUT_REJECTED`（纯文本模型拒收图片）：loop 撤下图片后重放，只武装一次，且要求
被拒请求确实带图。与其余信号一样 legacy 不安全，由 legacy_error_type() 映射为 invalid_request。

# contracts/errors — 九类错误封闭词汇(A5)

## 2026-09-10（B-03）— 新增 OUTPUT_TRUNCATED

第九类值,同批加进 loop.py:一个 step 零文本、零工具调用地收在
`stop_reason in {"length","max_tokens"}` 上,是「thinking 吃光了 max_tokens」的
证据(NetMind DeepSeek-V4-Pro 实测)。循环在**能真正加大请求时**把地板乘数翻倍重放一次
(`_truncation_retried` armed once,同 PREFILL_REJECTED 的「修复只武装一次」纪律);
翻倍后仍空,或翻倍加不大请求(ceiling 已等于地板、用户钉了 max_tokens),才用本类型
`_fail()` 落地成真失败(否则就是 B-05 那种「completed 但没输出」)。同样 legacy 不安全,
走 legacy_error_type() 兜底 invalid_request。

## 2026-09-03（插件平台批 1）— 事件常量改从 `narranexus.contracts.agent_events` import

`agent_framework/loop/events.py` 已删除（无兼容垫片，铁律 #2），本文件对事件字典常量/构造器的
引用全部指向契约包；语义与线上值不变（`tests/snapshots/golden/agent_events.json` 钉住）。

前六类镜像 loop/events.py 的 CLI_ERROR_TYPES;后四类是**先当信号处理,失败才落地**——循环先修请求再重放该 step:CONTEXT_OVERFLOW 触发被动压缩,PREFILL_REJECTED 触发追加续写 user 轮,OUTPUT_TRUNCATED 触发预算翻倍重放(仅一次),IMAGE_INPUT_REJECTED 触发撤图重放(仅一次)。四者都 legacy 不安全,由 legacy_error_type() 兜底映射 invalid_request,保证新词汇永不漏给旧消费链。

PREFILL_REJECTED 的 retryable 特意为 False:重放的是**改写后**的请求,原样重试只会再撞一次同一个 400。
