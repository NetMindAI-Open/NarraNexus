---
code_file: src/narranexus/platform/schema/turn_profile.py
stub: false
last_verified: 2026-09-04
---

## 2026-08-14 — durable 白名单改挂 WorkingSource.is_from_human（#307 🟡3）

`fast_for` 不再「非 voice 一律 durable」：durable 只给
`WorkingSource.is_from_human()` 为真的持久人类聊天面（chat/lark/slack/
telegram/wechat/narramessenger/discord/manyfold/a2a），job/message_bus/
callback/skill_study 与非枚举 source（"voice"）显式 ephemeral——与 step
层兑现 durable 的 `_is_user_chat` 同一谓词，声明与执行不可能分岔（否则
bus/Team 未来 opt-in 时 durable 声明+裸跑兑现=刚修掉的 C1 原样复活）。

## 2026-08-14 — narrative_persistence：miss 语义按 surface 分化（预审 C1/I4）

新字段 `narrative_persistence: Literal["ephemeral","durable"] = "ephemeral"`，
只被 bm25_top1 快路径消费。voice 保持 F28 ephemeral 契约（miss 裸跑、零
session 写）；`fast_for` 对非 voice surface 一律置 "durable"——聊天是持久
面，miss 必须 CRUD 建 narrative 且锚定 session，否则整 turn 从历史消失
（两个 history 端点都按 narrative 检索）。工厂内 `source == "voice"` 判断
即是这条 per-surface 策略的唯一落点。

## 2026-08-14 — 通用工厂 fast_for(working_source)

`fast_for(working_source, *, reasoning_effort="low")` 成为「fast 意味着哪些
knobs」的**唯一事实源**：BM25 top-1 / nexus_power / FULL prompt / effort=low /
include_arg_deltas / expression_nudge，`name=f"{source}_fast"`（接收
WorkingSource 枚举成员或裸字符串，取 `.value` 优先）——timing 日志天然按
surface 区分，未来 trigger 传 `fast_mode=True` 即自动获得自己的 profile 名。
`voice_fast()` 收编为 `fast_for("voice")` 的薄别名（knobs 逐字段不变，既有
锁测试保持绿）。首个新消费方：chat WS 链路（AgentRuntime.run 的
`fast_mode` 布尔 → `_resolve_turn_profile` → `fast_for`）。

## 2026-08-13 — voice_fast 增 expression_nudge=True

新字段 `expression_nudge: Optional[bool] = None`（None=TurnOptions 默认关）；voice_fast 置 True——语音轮哑轮补救 opt-in（机制见 nexus_power loop.py.md）。

## Why it exists

F28 语音快速回答模式的**每 turn 旋钮束**：一个 frozen pydantic 值对象，沿既有
纯 kwargs 链（run_stream → AgentRuntime.run → RunContext → TurnInput →
driver kwargs → executor wire）整体传递，替代散落的布尔开关。

## Design decisions

- **缺省=现状**是硬契约：`profile=None` 与「全默认值的 profile」对每个消费者
  必须不可区分（tests/schema + tests/agent_framework/test_nexus_turn_profile
  钉住）。fast mode 是纯加法。
- **全参数化带默认值**（Owner 2026-08-06 明确要求）：narrative 策略 / prompt
  面 / reasoning 档位 / 回复工具全部是字段，不硬编码不埋 env。
- `voice_fast()` 工厂承载 v1 决议：FULL prompt（不裁上下文）、工具面不裁、
  reasoning low（网关 DeepSeek reasoning 参数 2026-08-06 就绪）、强制
  nexus_power、reply_tool=speak。
- 跨 executor wire 用 `model_dump()` dict；nexus adapter 收 dict 或模型均可
  （单点归一化）。

## Downstream

nexus_agent._build_request_payload（prompt_mode / reasoning_effort→llm_extra /
include_arg_deltas）、step_3（framework_override）、后续 step_1 fast 分支
（narrative_strategy）与 trigger voice 检测（构造方）。
