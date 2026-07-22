---
code_file: src/xyz_agent_context/agent_framework/provider_driver/drivers/system.py
last_verified: 2026-07-20
stub: false
---
## 2026-07-20 — 删除死钩子 on_call_completed（行为不变）

本驱动此前有一个 `on_call_completed` 覆盖，声称"每次调用后扣减
`user_quotas`"，模块 docstring 和本文档也都这么写。审计发现**它从未被
调用过** —— 全仓（含 base.py 的 Protocol 声明与 `_DriverBase` 默认实现）
只有定义，没有任何 dispatcher 触发它。真正的扣减一直发生在
`utils/cost_tracker.py` 的 `record_cost`，依据 `provider_source` 上下文
标签，与 `cost_records` 写入同处。

即：下面 2026-05-13 那段"未来 Phase 1.5 让 cost_tracker 额外扣减"的设想
**早已落地并成为唯一实现**，而 Phase 1 的驱动层占位实现被遗留了下来。

删除而非仅修正注释，理由是它是**会花钱的地雷**：若将来有人给所有 driver
统一接上这个钩子，本驱动会与 cost_tracker 的钩子**双重扣费**。base.py 与
本文件的 docstring 已写明这一约束。同批删除的还有 `CallContext`（仅服务于
该钩子）及其包导出。

若将来真要把扣费职责搬回驱动层（可观测性审计建议的方向），**必须同时摘掉
cost_tracker 里的 deduct**，否则双重扣费。

## 2026-07-18 — 模块 docstring 门禁描述更新（行为不变）

门禁第 2 条从"用户已 opt-in（prefer_system_override）"改写为"quota 行存在
即授予"——该列自 2026-07-18 起仅是通知闩锁（[[provider_resolver]]），本
驱动的注册/解析逻辑本就不读它，纯文档修正。

## 2026-06-10 — build_anthropic_helper_config

Implements the new helper-slot builder for anthropic-protocol rows
(guarded the same way as build_claude_config). Lets this card serve
the helper_llm slot directly via the Messages-API helper.


# system.py — cloud-only system free-tier pool driver

The only Driver registered conditionally — guarded by
``is_cloud_mode()``. Local DMG / ``bash run.sh`` installs skip the
``register()`` call, so a misconfigured row with
``driver_type='system_pool'`` on a local DB raises a loud
``LLMConfigNotConfigured`` in the resolver instead of half-working.

## What's different from user-pays drivers

**在驱动层：没有区别。** 本驱动和 user-pays 驱动一样，只负责按 card 构造
凭证（`build_claude_config` / `build_openai_config` /
`build_anthropic_helper_config`），不参与计费。

计费差异发生在**驱动之外**：`utils/cost_tracker.py` 的 `record_cost` 在
`provider_source` 上下文标签为 `"system"` 时，从 `user_quotas` 扣减 token，
与 `cost_records` 写入在同一处。扣减失败只记日志不抛 —— LLM 调用已经成功，
不该因为记账写入抖动而让用户的请求失败。

⚠️ 该扣减**只看上下文标签，不看实际用了哪张 card**。这是已知的结构性弱点
（标签与事实可能分叉），见 2026-07-20 的配额审计。改动此处前先读
[[cost_tracker]] 与 [[provider_resolver]]。

## Where the credential comes from

Cloud migration (Phase 3) inserts a ``user_providers`` row with
``owner_user_id IS NULL`` and ``driver_type='system_pool'``, copying
the values from the existing ``SYSTEM_DEFAULT_LLM_*`` env vars.
Once that's in place, any user whose slot binding points at this
row routes through SystemDriver.
