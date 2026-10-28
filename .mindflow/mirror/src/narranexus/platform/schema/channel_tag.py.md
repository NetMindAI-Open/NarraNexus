---
code_file: src/narranexus/platform/schema/channel_tag.py
last_verified: 2026-08-26
stub: false
---

## 2026-08-26 — 新增 `is_agent_peer` + `format()` 渲染标记

「对面是不是另一个 agent」由 [[channel_trigger_base.py]] 的 `is_agent_peer`
seam 填。**带在 ChannelTag 上而不是下游现推**：只有 trigger 层知道各平台的
身份约定，而需要这个答案的地方（先是 prompt，之后是回复决策层）都在
`ParsedMessage` 够不到的高度。

`format()` 在为真时追加 `AGENT_PEER_MARKER`（`agent sender`）——这是模型
**唯一**能看到这个信号的途径。选 `format()` 而不是在四个 `tagged_prompt`
拼接点各加一行，是因为那正是「N 份手抄」缺陷类；`format()` 是唯一定义。

**只在为真时追加**：这些字符串也会进聊天历史，无条件改格式会让新旧轮次
前后不一致。`to_dict()` 本来就丢假值，所以已有的序列化 tag 一字不变。

`parse()` 一并改：先剥掉尾部标记再按位置读，否则一个**没有 room_id** 的
agent tag 会把 `agent sender` 当成 room_id。

# channel_tag.py

## Why it exists

Every message entering `AgentRuntime` needs to carry a label identifying where it came from — a human typing in the UI, a cron Job firing, a Matrix room message, a future Slack integration. Without a unified identifier, modules like `SocialNetworkModule` and `NarrativeService` would need to inspect different fields depending on the trigger path. `ChannelTag` provides that single carrier, injected at the trigger boundary and threaded through the entire execution context.

It lives in the shared schema layer rather than inside any module because it is infrastructure: every trigger type produces one, and multiple modules consume it.

## Upstream / Downstream

**Producers** (one per trigger): direct chat routes produce `ChannelTag.direct(...)`, `JobTrigger` produces `ChannelTag.job(...)`, `MatrixTrigger` produces `ChannelTag.matrix(...)`. Each trigger is responsible for constructing an appropriate tag before calling `AgentRuntime`.

**Consumers**: `SocialNetworkModule.gather()` reads `ChannelTag` to identify which entity to look up or create in the social graph. `NarrativeService` stores the serialized tag in chat history so conversation context carries source information. The agent's system prompt may include the formatted tag string to give the LLM situational awareness about who is sending the message.

## Design decisions

**`@dataclass` instead of Pydantic `BaseModel`**: `ChannelTag` is a lightweight, in-memory-only value object. It is never persisted directly to a database row. Using `@dataclass` keeps it simple and avoids Pydantic validation overhead for a structure that is always constructed from trusted internal code.

**`format()` returns a bracketed dot-separated string** like `[Job · Daily Report · job_daily_report_001]`. This format was chosen so the tag can be embedded verbatim into the agent's prompt text. The LLM sees the structured label inline without requiring a separate formatting step. The `parse()` static method provides the inverse for cases where the tag is read back from stored text.

**`room_id` and `room_name` default to empty string rather than `None`**: this avoids `Optional` checks in callers that always want a string for prompt injection. Empty string serializes away cleanly in `to_dict()`.

## Gotchas

**`to_dict()` silently drops empty fields** — `room_id=""` is omitted from the serialized dict. This means `from_dict(tag.to_dict())` will reconstruct the tag correctly (empty string is the default), but if you inspect the stored dict expecting `room_id` to be present with an empty value, you will not find it.

**`parse()` returns `None` on failure** without raising. Any code that calls `parse()` on a stored string must handle the `None` case — the string might have been hand-edited, truncated in storage, or come from an older format that predated the `·` separator.

## New-joiner traps

- The `channel` string values are lowercase (`"direct"`, `"job"`, `"matrix"`). The `format()` method capitalizes them for display. Do not compare against capitalized values.
- `ChannelTag` has no relationship to `InboxMessageType.CHANNEL_MESSAGE`. They overlap in concept (both describe message sources) but are completely separate mechanisms — `ChannelTag` is a runtime tag on agent input; `InboxMessageType` is a classification for stored inbox messages.
