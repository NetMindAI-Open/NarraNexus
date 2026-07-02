---
code_file: backend/routes/agents_bus_failures.py
last_verified: 2026-07-02
stub: false
---

# agents_bus_failures.py — MessageBus 永久失败列表 + 重试恢复路由

## 为什么存在

`LocalMessageBus.get_pending_messages`（`local_bus.py`）在一条消息的
`bus_message_failures.retry_count` 达到 3 时会把它永久过滤掉——这是防
poison-message 的机制，但代价是消息从此彻底消失，没有任何 UI 或 API 能看到它、
更谈不上恢复。`MessageBusTrigger._notify_permanent_failure`
（`message_bus_trigger.py`）解决了"用户完全无感"的一半问题（往 inbox 写一条
通知），但通知之后用户仍然需要一个动作入口去清掉失败记录、让消息重新被投递
——这个文件就是那个入口。是 NetMindAI-Open/NarraNexus#52 修复的"可恢复"半边。

独立成文件而不是塞进 `inbox.py` 或某个 `bus.py`，是因为它严格遵循
`agents_cost.py` 建立的"per-agent 子资源路由 + 所有权校验"惯例——`agents.py`
把这类文件都聚合在 `/api/agents/{agent_id}/...` 命名空间下，帮它挂进去比另开
一套顶层路由更符合项目现有模式。

## 上下游关系

- **被谁用**：`backend/routes/agents.py`（`router.include_router(bus_failures_router)`，挂载在 `/api/agents` 下）；前端目前**没有**对应 UI（本 PR 只交付后端路由，前端 follow-up）
- **依赖谁**：
  - `backend.auth.resolve_current_user_id` — 拿 viewer 身份（同 `agents_cost.py` 模式）
  - `xyz_agent_context.utils.db_factory.get_db_client` — 直接查 `bus_message_failures` / `bus_messages` / `agents` 表
  - 不直接依赖 `LocalMessageBus`——重试端点只是删除 `bus_message_failures` 行，下一次 `MessageBusTrigger` 轮询会自然通过 `get_pending_messages` 把消息捞回来

## 设计决策

**重试 = 删记录，不是重新入队**：`retry_bus_failure` 只是 `DELETE FROM
bus_message_failures WHERE message_id=... AND agent_id=...`，不主动触发
`AgentRuntime`。这是安全的，因为失败路径从不调用 `ack_processed`（只有
`_handle_channel_batch` 的成功分支会推进游标——见 `message_bus_trigger.py`），
所以失败消息对应的 `bus_channel_members.last_processed_at` 游标从未越过它；
删除失败记录后，下一次 `MessageBusTrigger` 轮询（默认 3-12 秒自适应间隔）
自然会把消息重新纳入 `get_pending_messages`。被否决的方案是重试端点直接调用
`AgentRuntime`——那样会重复实现 `_handle_channel_batch` 的整套 prompt 构建 /
owner-relay / team-chat 分支逻辑，而"等下一次轮询"的延迟只有几秒，不值得。

**鉴权照抄 `agents_cost.py`**：viewer_id 只信 session（拒绝 `?user_id=`
query param），单 agent 强制 `agents.created_by == viewer_id`，失败统一 404
（不是 403，不泄露 agent 是否存在）。这个文件没有引入新的鉴权模式，是刻意的
——项目里已经有一个验证过的 per-agent 所有权校验模式，复用比发明新的更安全。

**`retry_count >= 3` 而非任意失败都返回**：`list_bus_failures`
只列出真正"永久失败、被 poison filter 挡住"的消息（这正是需要人工介入的那批），
不列出 1-2 次瞬时失败还有机会自愈的消息——避免给用户一堆噪音。

## Gotcha / 边界情况

- **触发**：调用 retry 端点前如果 `message_id`/`agent_id` 组合在
  `bus_message_failures` 里不存在 → **症状**：404 `"Failure record not
  found"` → **根因**：`db.get_one` 存在性检查先于 `delete`，防止对不存在的
  失败记录返回一个假的 "success"。
- **触发**：非 owner 调用 GET/POST → **症状**：404（不是 403）→
  **根因**：`_require_owned_agent` 统一伪装成"agent not found"，与
  `agents_cost.py` 的 defense-in-depth 理由一致——不向未授权调用方泄露 agent
  是否存在。

## 新人易踩的坑

这个文件**不会**重新触发 `AgentRuntime`——它只清掉失败记录。如果你以为调用
retry 端点后消息会立刻被处理，实际上要等 `MessageBusTrigger` 的下一次轮询周期
（生产环境默认 3-12 秒自适应间隔），不是同步的。

前端 UI 未随本 PR 交付——`GET /{agent_id}/bus-failures` 和
`POST /{agent_id}/bus-failures/{message_id}/retry` 目前只能通过 API 直接调用；
frontend follow-up 见 PR body。
