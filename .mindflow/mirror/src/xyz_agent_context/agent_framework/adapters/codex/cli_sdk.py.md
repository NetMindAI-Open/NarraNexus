---
code_file: src/xyz_agent_context/agent_framework/adapters/codex/cli_sdk.py
stub: false
last_verified: 2026-08-04
---

## 2026-08-04 — v1 fallback 的 "headers ignored" 告警豁免平台 header

与 v2 同批:每个模块 server 现在都带 `X-NarraNexus-*`(身份注入),原来的
告警会每轮列出全部 ~16 个 server 名。改为只在**用户自己的** header 被丢时
告警。判据是命名空间前缀而非 import 常量(适配层不反向依赖 module 包)。
v1 未注册、仅作 revival fallback,影响面小但和 v2 保持一致。


## 2026-07-27（补）— review 修复：@timed 归位 agent_loop

PR #167 review 抓到：插入 capabilities() 时错位到了 @timed 装饰器与
agent_loop 之间，`llm.claude.agent_loop`/`llm.codex.agent_loop` 延迟埋
点静默丢失且指标被误挂到 capabilities()。已把 capabilities() 移到装饰
器上方；契约测试新增 `test_agent_loop_keeps_timed_instrumentation`
（断言 agent_loop 有 __wrapped__、capabilities 没有）防回归。


## 2026-07-27 — `_build_system_prompt_and_user_msg` 移入 materializer（flatten_for_file）

函数体逐字迁至 [[materializer.py]] `flatten_for_file`（本文件与
official_sdk 的调用点同步改名）。原函数 NOTE 里说的「合并为共享 helper
是后续任务」即本次。拷贝语义（不变异调用方）不变。


## 2026-07-27 — 取消检查统一走 CancellationView（codex v2 死代码修复）

轮询式取消检查改为 `CancellationView(cancellation).requested()`。对
claude/cli_sdk/remote 是等价替换；对 codex v2 是 bug 修复——原
`getattr(cancellation, "is_set", lambda: False)()` 对真实
CancellationToken 恒 False（token 只有 is_cancelled property），进程内
codex turn 此前根本无法被打断。测试
`tests/agent_framework/test_cancellation_view.py` 含该回归用例。


## 2026-07-27 — driver 表面一致化：capabilities() 空协商缝 + 签名整形

三个 driver（claude / codex v1+v2 / remote）统一新增 `capabilities() ->
set[str]`（全部返回空集 = 今天的行为；词汇表见 driver.py 注释，只在能力
真正实现的同一变更里声明）。`streaming` 全员改 keyword-only（所有调用点
本就关键字传参，零行为变化）。codex v2 的 `del kwargs` 改为显式 WARNING
（此前 `disallowed_tools` 被静默丢弃——调用方以为约束生效了）。契约测试
`tests/agent_framework/test_driver_contract.py` 钉住整个表面。

## 2026-07-27 — 事件类型字面量收敛到 loop/events.py 常量

六种事件形状的字符串字面量改为 import `loop/events.py` 的常量
（TYPE_RAW_RESPONSE_EVENT 等），值逐字节不变——纯机械替换，行为零变化。
事件契约自此有唯一事实源，详见 events.py.md。

