---
code_file: src/xyz_agent_context/module/narramessenger_module/narramessenger_context_builder.py
stub: false
last_verified: 2026-07-20
---

## 2026-07-20 — roster-on-demand now via `narra_cli`

Intent unchanged (``get_room_members`` still returns ``[]``; roster is not
prompt-injected). Only the on-demand path was renamed in the docstrings: the
removed ``narra_room_members`` MCP tool is replaced by
``narra_cli("room info --room-id <id> --members")``. See
[[_narramessenger_mcp_tools]] 2026-07-20.

## 2026-07-09 (later) — `get_conversation_history` + `get_room_members` → `return []`

死路径清理。Pre-Matrix（Gateway/polling 年代）NarraMessenger 后端会在每次 invocation payload 里塞：
- `context`（DM）/ `group_context.history_messages`（Group）→ 本文件的 `get_conversation_history` 读它、正规化后供 base `_build_history_section` 拼进 `## Conversation History`
- `group_context.members` → `get_room_members` 读它、供 base `_build_members_section` 拼进 `## Conversation Members`

Direct Matrix 迁移（Commit 7，2026-07-02）后，我们直接从 `/sync` 拿 raw `m.room.message` 事件，`matrix_trigger._wrap_event` 返回的 dict 里**既没有 `context` 也没有 `group_context`**。两个方法都走 fallback、返回空 list。这套 fallback 逻辑存在了 6 天，运行时行为一直是"返回空 list、channel prompt 里没有对应 section"。

2026-07-09 直接把两个方法的方法体都压缩到 `return []`，同步 module-level docstring（原来说"从 ParsedMessage.raw 直接读"，与实际实现矛盾——review #3 命中的那条 stale docstring）。行为不变，代码不再撒谎。

当前 turn 的附件 marker 现在由 [[context_runtime.py]] `build_input_for_framework` 在组装 LLM-facing user message 时注入，本 builder 不参与——marker 只进 LLM 视图，不动 `ctx_data.input_content`，因此不会污染持久化 content 和前端 chat 面板回显。历史 turn 的附件仍靠 ChatModule 在 `hook_data_gathering` 遍历历史时合成，跟当前 turn 共用同一个 `Attachment.markers_from_dicts` 底层（见 [[attachment_schema.py]]），格式一致。

live roster 现在通过 `narra_room_members` MCP 工具按需查（见 [[_narramessenger_mcp_tools]]），不再无脑塞进每次 prompt。

Regression：`tests/narramessenger_module/test_context_builder_no_inline_history.py` 4 条（`get_conversation_history` 裸 Matrix / legacy shape 各 1；`get_room_members` 裸 Matrix / legacy shape 各 1）。

## 2026-07-09 — reply_instruction drops the `narra_progress` clause

Companion cleanup to the silent-first refactor on [[matrix_trigger.py]]
(2026-07-08). The `reply_instruction` string used to include:

> "For genuinely long work you MAY call `narra_progress(text="…")` first
> with a few-word status — it updates the sender's 'thinking' message in
> place."

That whole clause is gone. There is no thinking placeholder anymore —
the room stays quiet until `narra_reply` fires — so telling the agent
otherwise burned a tool call, spent tokens, and let the model believe
it had already communicated a status to the sender. Prompt now tells
the agent explicitly: *the room stays quiet until you call
`narra_reply`; if the work takes a while, just do it and send the
final answer when ready*.

The `narra_progress` tool itself was removed in the same PR (see
[[_narramessenger_mcp_tools]] 2026-07-09 section).

## 2026-07-03 — reply_instruction points at `narra_reply`

`reply_instruction` / `send_tool_name` changed from `narra_send` to
**`narra_reply`** (+ a line about `narra_send_media` for attachments). This is
what tells the agent to use the trigger-delivered reply marker instead of the
old Gateway `/chat/send` tool — the prompt half of the send unification (see
[[_narramessenger_mcp_tools]] / [[matrix_trigger.py]]).

## Why it exists

Assembles the per-turn execution prompt for a NarraMessenger message via the
`ChannelContextBuilderBase` Template Method. Unlike Telegram (which keeps its
own `bus_messages` history), NarraMessenger ships conversation context INLINE
in every invocation, so this builder reads straight from `ParsedMessage.raw`.

## Design decisions

- **`get_conversation_history` reads the invocation payload**: group →
  `group_context.history_messages`; DM → `context` (`[{role, sender, content}]`).
  Either is normalised to `[{sender, timestamp, body}]`. A trailing entry that
  duplicates the current trigger message is dropped (the template renders the
  current message separately).
- **`get_room_members` reads `group_context.members`** (matrix_user_id +
  display_name). DM → empty (base hides the members section for ≤2 anyway).
- **`send_tool_name="narra_send"`** + a `reply_instruction` telling the agent
  to call `narra_send(room_id=..., text=...)`.
- **`my_channel_id = credential.matrix_user_id`** so "Me (agent)" rows in
  history are labelled correctly.

## Upstream / downstream

- **Upstream**: `ChannelContextBuilderBase` (`build_prompt` / `build_retrieval_anchor`).
- **Reads**: `ParsedMessage.raw` (the full invocation), the credential's
  `matrix_user_id`.

## Gotchas

- History fidelity depends entirely on what the platform put in `context` /
  `group_context`. `compressed_context` may be `null` and `context_state` may
  flag gaps — for v1 we use whatever is provided and do not call
  `/agent-context/group` separately (gateway already inlines it).
