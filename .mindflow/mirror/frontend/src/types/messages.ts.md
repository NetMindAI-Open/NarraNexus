---
code_file: frontend/src/types/messages.ts
last_verified: 2026-08-01
stub: false
---

## 2026-08-01 — 两个联合类型补 `free_tier_exhausted` / `invalid_credentials`

`ErrorMessage.action_reason` 与 `ChatMessage.actionReason` 补上这两个取值。
`invalid_credentials` 是 2026-07-29 那次（BYOK NetMind key 被 403 拒、整轮落到伪造
兜底回复）加进后端分类器的，当时**没同步过来**；`free_tier_exhausted` 是本次新增。

值得记的是**为什么会漏两次**：两个联合末尾都有 `| string` 前向兼容，所以后端加了新
reason 而前端不补，`tsc` 一声不吭 —— 这份枚举实际上退化成了装饰，IDE 补全和 switch
穷尽检查都靠不住。`| string` 要留（wire 契约必须容忍比前端新的后端），但代价就是
**同步只能靠人记**：改 `failure.py` 的 `SELF_SERVICEABLE_REASON_*` 时，这里没有编译器
替你报警。

## 2026-07-30 — Segment 型别落户 types + `ToolCallEvent.pending` + `ChatMessage.segments`

- `ChatMessage.segments`：stopStreaming 把切好的段挂在消息上，气泡按段
  渲染；老消息 undefined 回落 content 单段。content 保留 join 全文
  （通知/复制/搜索的纯文本载体）。
- `Segment` / `SegmentReply` / `ProcessEvent` 定义在这里而不是
  `lib/segmentTurn.ts`：types 不能反向依赖 lib，切段函数从 types 导入。
  `Segment` 是「一轮的一个用户可见片段」——process（导致它的思考/工具）
  + reply（可为 null：整轮零回复时过程不丢）。
- `ToolCallEvent.pending`：工具名已到、参数还在流式生成中。名字一到就
  发 `pending=true` 的事件，参数齐了发同 `tool_call_id` 的完整事件覆盖。
  不支持名字先行的框架只发一次完整事件（缺省即假），消费端无需分支。

## 2026-07-29 — NexusPower 专属的两个消息型别

`agent_reply_delta`(表达工具参数流=真正的"agent 在说话")与 `agent_plan`
(整份快照)写进 `MessageType` 联合。只有一个框架发的形状照样要进联合——
否则每个消费点都得写 cast。其他框架永不发这两种,消费端按存在与否分支即可。

## 2026-07-22 — action_reason/actionReason 补 executor-infra reasons

`ErrorMessage.action_reason` 与 `ChatMessage.actionReason` 的联合类型补上
`'executor_oom' | 'executor_unreachable'`（仍保留 `| string` 前向兼容），注释说明
两类 error_type（`config_actionable` 用户可修 / `infra_transient` 平台侧）都会带
reason 且都 skip 兜底。纯类型+注释，无运行时逻辑。

## 2026-07-21 — BusAttachment voice fields

`BusAttachment` gained `source?: 'recording'|'upload'` and `transcript?` for team voice
memos (rendered as a transcript, read by agents via the marker).

## 2026-07-20 — BusAttachment type

Added `BusAttachment` (file_id/mime_type/original_name/size_bytes/category/rel_path)
for files carried on message-bus messages. Distinct from chat `Attachment`: addressed
by `rel_path` in the per-user shared area, not per-agent `file_id`. Used by
`TeamChatMessage` / `RoomMessage` and [[BusAttachmentList]].
# messages.ts — 前端运行时消息 + ChatMessage 类型契约

## 为什么存在

后端 `AgentRuntime` 以流式 `yield` 各类 RuntimeMessage（progress /
agent_response / agent_thinking / tool_call / error / complete …），WS 层把它们
`to_dict()` 后推给前端。本文件是这些 wire 消息的 **TypeScript 镜像**（对齐
`schema/runtime_message.py`），外加前端自己的 `ChatMessage`（会话/历史里持久化
的一条消息，由 chatStore 从 wire 消息组装而成）。它是 producer/consumer 的
稳定契约:字段漂了这里就报错。

## 关键类型

- `ErrorMessage`:wire 错误帧。`severity`（fatal/recoverable/recovered/
  recovered_after_reply）决定前端如何渲染。
- `ChatMessage`:UI 层一条消息，带 `isError` / `warnings` / `timeline` 等
  展示派生字段。MessageBubble 直接读它，不读 live session。

## 2026-07-14 — 确定性自助类错误字段（"黑盒" P1）

- `ErrorMessage.action_reason?`:仅当 `error_type === 'config_actionable'`
  时设置，取值 `context_window` / `insufficient_balance` / `model_not_found`
  （开放字符串，向后兼容新原因）。对应后端 `SELF_SERVICEABLE_ERROR_TYPE`。
- `ChatMessage.actionReason?`:chatStore 在 `stopStreaming` 时从
  `currentActionReason` 盖上（仅 `isError` 时），供 [[MessageBubble.tsx]] 渲染
  "你可以做什么"面板而非笼统失败。

这类失败（上下文太小/余额/模型 ID）确定性、可自助修复，后端不再让 helper
兜底掩盖，前端据此给可操作引导。
