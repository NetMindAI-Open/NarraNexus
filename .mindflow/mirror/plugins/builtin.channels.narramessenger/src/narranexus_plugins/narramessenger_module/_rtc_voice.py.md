---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/_rtc_voice.py
stub: false
last_verified: 2026-08-11
---

## Why it exists

F28 语音快速回答模式（voice fast mode）的入站契约层：Hybrid 把 final STT
以人类 Matrix 身份发成普通 `m.text`，同一事件附带
`ai.netmind.rtc.voice_input` v1 metadata。本文件是该契约在 Nexus 侧的
**唯一**解析与校验点——trigger（`matrix_trigger._wrap_event` /
`parse_event`）只调用这里的纯函数，不自己碰 metadata 字段。

契约源：Hybrid「Direct Matrix RTC 快速回覆」handoff（Lark wiki
`Gr9Qwkes2iCF2VkrbmslBpQrgih`）§3.1/§3.2/§3.4/§4.2；方案全貌见 PRD
`WTHSdPt4topN7YxnXPcl4wV3gRd` §4.3。

## Design decisions

- **两级触发契约。** 严格解析（`parse_rtc_voice_input`）成功 →
  完整 voice turn，带四个 correlation ID（`rtc_session_id`/`turn_id`/
  `invocation_id`/`agent_profile_id`）。严格解析失败但 metadata 里仍带
  非空白 `voice_instructions` 字符串（`extract_common_voice_instructions`，
  即"common trigger"）→ 降级 voice turn：有语音模式、无 correlation。
  两者都没有 → 按普通文字消息处理。三种情况下都**绝不中断正常回复路径**。
- **严格校验、宽松失败。**§3.2 的每条规则都是 `===` 语义：`version`/`seq`
  用 `type(x) is int` 排除 bool（Python 里 `True == 1`，直接 `==` 会把
  `version: true` 放进来——变异测试钉住了这一点）；`transcript_final`
  必须 `is True`。任何一条不满足返回 `None`，上游把事件当普通文字消息，
  **绝不因坏 metadata 中断正常回复**。
- **voice_instructions 是唯一宽容字段**：缺失/空白/类型错 → 降级为
  `None`，voice turn 本身仍然合法（契约明文要求）。
- **common trigger 只认 metadata，不碰 body。**
  `extract_common_voice_instructions` 复用与 `parse_rtc_voice_input` 相同
  的事件壳校验（`m.room.message` + `m.text` + metadata 是 dict），但跳过
  §3.2 的四个 ID / version / seq 强字段——它是严格解析失败之后的兜底
  通道，故意不去解析 body envelope，保证模式判定不依赖 body 字符串解析。
- **`transcript_final is False` 是 common trigger 的唯一 carve-out
  （2026-08-11 review）。** 后端显式标 "not final" 是 turn 边界信号，
  不是坏数据：interim STT 片段绝不能进降级 voice 模式（会被当成完整
  utterance 念出来）。判定是 **identity**（`is False`）——缺失/类型错
  （含 `0`、`"false"`）仍算坏数据、照走 common trigger。
- **降级路径的 instructions 有 2000 字符上限**
  （`COMMON_INSTRUCTIONS_MAX_CHARS`）：common trigger 放宽了构造门槛，
  无界字符串会白白撑大 prompt；严格路径由契约本身约束、不截。
- **metadata 不是授权凭证**：本层只做识别，不做任何权限判断；调用方必须
  保留既有 sender/room 校验（authorize-event 门禁照过）。
- **envelope 只认开头**：`split_narra_system_prompt` 仅剥离 body 起始处的
  `<narra-system-prompt>` envelope；未闭合、出现在正文中间的一律不剥，
  整个 body 按 transcript 处理——用户原文永远不能被提升为指令内容。

## Downstream

被 trigger 的 voice 检测分支消费（fast profile 触发 + envelope 注入；
通话级串行自 2026-08-11 起按 agent:room key，不再取 rtc_session_id）。`parse_rtc_voice_input` 与
`extract_common_voice_instructions` 自 2026-08-11 起是
`matrix_trigger._detect_voice_turn` 两级检测的 Level 1 / Level 2 判据；
两者共用私有 `_voice_metadata` 事件壳守卫（m.room.message + m.text +
metadata 是 dict），壳判定永不分叉。测试：
`tests/narramessenger_module/test_rtc_voice_input.py`（合法/全部非法变体/
envelope 边界，含 bool-as-int 变异防护；common trigger 的独立用例组
`TestExtractCommonVoiceInstructions`；`RTC_VOICE_INPUT_KEY` 字面量钉子）。
