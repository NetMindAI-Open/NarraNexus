---
code_file: src/narranexus/platform/narrative/wipe_service.py
last_verified: 2026-09-04
stub: false
---

# wipe_service.py — full "clear conversation & memory" wipe

## 为什么存在

清除一个 agent 的对话/记忆时,数据库只是索引;真正的长记忆正文在磁盘上
(`settings.narrative_markdown_path/<agent>/<user>/narratives/*.md`),逐轮轨迹在
`trajectory_path/.../trajectories/`。系统重启时会**从磁盘 .md 重建 DB**。所以历史上
只清 DB 的 `DELETE /history` 无效——重启后 agent 照旧记得。这个 service 把 DB + 磁盘 +
session 一起清干净,是 [[agents/chat_history.py]] 那个路由的实现层。

## 上下游关系

**被谁用**:`backend/routes/agents/chat_history.py` 的 `DELETE /{id}/history`,按
`?conversations=&memory=` 两个 flag 调用。

**依赖谁**:`InstanceRepository`(取 ChatModule 实例)、[[exporters.py]] 的
`NarrativeMarkdownManager.delete_all()` / `TrajectoryRecorder.delete_all()`(删磁盘)、
[[session_service.py]] 的 `delete_session()`(删 `~/.narranexus/sessions` 文件)、
`schema_registry.MEMORY_KINDS`(统一记忆表清单)。

## 设计决策

**两个独立范围**:`clear_conversations`(narratives/events/event_stream/
module_report_memory/instance_narrative_links/chat 实例及其记忆/agent_messages + 磁盘
narratives+trajectories + session)与 `clear_memory`(`memory_*` 七表 +
memory_consolidation_queue + instance_artifacts)。前端复选框可任选其一或都选;都选=彻底失忆。

**`memory_*` 只能按 `agent_id` 清**:这七张表共用一套 schema,只有
`agent_id + scope_type + scope_id`,没有 user_id / narrative_id。agent 是单属主
(`user_id = agent.created_by`),所以按 agent_id 清是安全且多租户正确的;代价是记忆是
整 agent 全清,无法按单条 narrative 隔离清除。

**MessageBus 渠道历史(`bus_messages`)在 conversations 范围内会清**,但只清该 agent
**独占(单成员)**的渠道(它的 lark/telegram/wechat 私聊 DM)。这是关键:agent 会调
`bus_get_messages(agent_id, channel_id)` 直接从 bus 镜像重建"各渠道聊过啥"——不清它,
"已清空"的 agent 仍能全盘复述(2026-07-10 实测正是此路径泄漏)。多 agent 共享渠道**不动**
(删了会毁掉其他成员的历史)。渠道绑定/成员(`bus_channels`/`bus_channel_members`)保留,
agent 继续能收新 IM;只删消息行。**平台侧不可及**:Lark/Telegram/WeChat 服务器上的原始
消息删不掉,直连平台 API 的工具仍能读到——这是产品必须向用户说明的边界。

> **已被取代（2026-08-18）**：以上描述的是 2026-08-17 之前的机制。`bus_get_messages` 与
> `ChannelInboxWriter` 均已删除，IM 记录搬到 `inbox_threads` / `inbox_thread_messages`。
> 本段保留为当时的事实记录 —— 不改写带日期的条目 —— 现行行为见本文件 2026-08-18 条目。

**刻意不碰**:`inbox_table`(user 级跨 agent)、`cost_records`(计费)。以及永远保留:
`agents`、凭证、`user_settings`、`instance_awareness`(人设)、系统/能力实例(只删
`ChatModule` 行)。

**DB 先提交,磁盘尽力删**:DB 删除在一个事务里;提交后再删磁盘,每步 try/except 收进
`disk_errors`,绝不因文件系统问题回滚 DB。整个操作幂等——重跑返回 0、不抛。

## Gotcha / 边界情况

- 删完默认 narrative 也没了,但**下一轮**会由 `_ensure_default_narratives`
  ([[retrieval.py]])自动空重建——所以清理后立即查库,默认 narrative 是缺失的,属正常。
- 磁盘删除路径含 `user_id`,必须来自鉴权,绝不能用请求方可控的 query 参数,否则会删到别人的
  磁盘目录。路由层已拒 `?user_id=` 并强制 owner。
- 并发:清理时若有活跃 run 正在写记忆,可能残留新行。用户在自己 agent 上手动触发,罕见,
  视为已知限制。

## 2026-08-18 — 「清空会话」此前对 IM 记录报告成功却什么都没清

本服务原本靠遍历 `bus_channel_members` 找出「只有本 agent 一个成员」的频道、删其
`bus_messages` 来清 IM 私聊历史。inbox 拿到自己的两张表、`ChannelInboxWriter` 被删之后，
IM 侧不再产生任何伪 agent / 频道 / 成员行 —— 循环什么也找不到，而
`inbox_threads` / `inbox_thread_messages` 不在清理清单里。

后果是一条**报告成功却不生效**的数据保留承诺，作用在系统里最大的内容类上，而且前端直接
渲染它（`/api/agent-inbox`）：用户点完「清空」拿到成功响应，重新打开面板整段 Lark/Telegram/
WeChat 对话还在。运维看不见，只会变成一张工单或一个合规问题。

现按 `agent_id` 清两张表（**不是** `owner_user_id` —— 一个用户可能有多个 agent）。先删消息
后删会话：反序会把消息留成没人能命名的孤儿，此后每次清理都到不了它们。依赖
[[inbox_recorder.py]] 的 `im_thread_id` 带 agent，否则同一个 chat 上两个 agent 共用一行、
按 agent 删会删掉别人的记录。

docstring 原来的论证引用 `bus_get_messages(agent_id, channel_id)` —— 本次一并删掉的工具，
即「文件的理由引用了不存在的机制，而它的代码指向不再被写入的表」。已重写。

## 2026-08-18 (二) — 计数器接到 API，并加一条覆盖测试

`WipeResult` 的计数器此前**止步于服务边界**：路由用手写的 kwargs 一个个列字段，而
`ClearHistoryResponse` 也是另一份列表 —— 同一个字段表存在三处，且已经漂过两次。

后果不是数字不好看：C3 修掉的缺陷是「报告成功却什么都没清」，而**报告**这一半仍然是瞎的 ——
`/clear` 返回的 inbox 计数恒为 0，所以将来某次回归让 inbox 删除静默失效时，响应体与一次成功的
清理**逐字节相同**。这正是 C3 当初能活下来的机制。下一张「我清空了但 Lark 历史还在」的工单，
拿到的响应体分不出这两种情况。

新增 `test_wipe_result_fields_reach_the_api`：断言每个 `*_count` 都出现在响应模型里**并且**
被路由真的填上（有位置放但没人填会静默默认 0）。它当场又抓出两处更早的遗漏 ——
`report_memory_count` 与 `instance_links_count`，删了但从不上报 —— 那是没人注意到、由测试
发现的。

## 2026-09-04 · chat instances by capability flag (batch 5b.2)

The removable per-conversation instances are those of any module that `provides_chat_history` — no `"ChatModule"` literal.
