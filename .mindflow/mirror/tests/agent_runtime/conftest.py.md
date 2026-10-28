---
code_file: tests/agent_runtime/conftest.py
stub: false
last_verified: 2026-08-26
---
# conftest.py — agent_runtime 测试的共享夹具

## `_reset_im_dm_fallback_history`（autouse，整目录）

DM 兜底限流器持有**模块级**状态（`_im_dm_fallback_history`）。重置放在
conftest 而不是单个测试文件里，是因为 `test_im_dm_fallback_delivery_e2e.py`
会跑一次真实投递，因而**在不知情的情况下**往那张 map 里追加条目。

只在测它的那个文件里重置的话，下一个断言这张 map 大小的测试会拿到一个
**依赖执行顺序的初值**——这类 flake 的调试成本远高于一个 autouse 夹具。

## 重置覆盖的是**三张** map，不是一张

`reset_im_dm_fallback_history()` 现在同时清 `_im_dm_fallback_history`（限流
滑窗）、`_fallback_audit_cooldown` + `_fallback_suppressed_since_row`（审计
面），以及 `_fallback_auditor` 这个惰性单例。

单例是最容易漏的一个：它在首次审计时创建并缓存，一个用例塞进去的 fake
auditor 会被下一个用例继续用，于是后者的断言实际测的是前者的替身。

## `capture_run_context`

`AgentRuntime.run()` 是个生成器，想看它**构造 `RunContext` 时传了什么**，
就得在构造那一刻把执行截停。夹具用一个构造即抛的 `_SpyCtx` 做这件事，抛的是
专用异常 `CtxCaptured` 而不是通用异常——否则任何真实错误都会被当成"截停成功"。

它继承的是 `BaseException` 而不是 `Exception`，**今天不承重**：捕获点在所有
`except Exception` 之外，降级成 `Exception` 也不会有任何测试变红。源码
docstring 专门标注了这一点，正因为它不承重才更要写下来——一旦捕获点挪进被
`except Exception` 覆盖的块里（比如往 `run()` 内部挪），一个可吞的哨兵会让
**所有基于这个夹具的测试无断言通过**，而且是静默绿。

`db_client=` 是可选的，**刻意不做成无条件桩**：一部分调用方要用 per-test 库
喂 agent 查询，另一部分要的恰恰是"没有自己的库时也能走到 `RunContext`"这条
覆盖。把桩打死会把后者悄悄删掉。

结尾的 `assert captured` 是这个夹具的自检：没构造过 `RunContext` 就断言失败，
而不是返回一个空 dict 让下游断言在空数据上"通过"。
