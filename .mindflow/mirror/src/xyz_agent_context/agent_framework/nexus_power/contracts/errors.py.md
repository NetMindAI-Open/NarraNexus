---
code_file: src/xyz_agent_context/agent_framework/nexus_power/contracts/errors.py
last_verified: 2026-07-31
stub: false
---
# contracts/errors — 八类错误封闭词汇(A5)

前六类镜像 loop/events.py 的 CLI_ERROR_TYPES;后两类是**信号不是失败**——循环先修请求再重放该 step:CONTEXT_OVERFLOW 触发被动压缩,PREFILL_REJECTED 触发追加续写 user 轮。两者都 legacy 不安全,由 legacy_error_type() 兜底映射 invalid_request,保证新词汇永不漏给旧消费链。

PREFILL_REJECTED 的 retryable 特意为 False:重放的是**改写后**的请求,原样重试只会再撞一次同一个 400。
