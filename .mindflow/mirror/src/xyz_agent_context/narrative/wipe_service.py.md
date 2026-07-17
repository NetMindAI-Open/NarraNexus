---
code_file: src/xyz_agent_context/narrative/wipe_service.py
last_verified: 2026-07-10
stub: false
---

# wipe_service.py — full "clear conversation & memory" wipe

## 为什么存在

清除一个 agent 的对话/记忆时,数据库只是索引;真正的长记忆正文在磁盘上
(`settings.narrative_markdown_path/<agent>/<user>/narratives/*.md`),逐轮轨迹在
`trajectory_path/.../trajectories/`。系统重启时会**从磁盘 .md 重建 DB**。所以历史上
只清 DB 的 `DELETE /history` 无效——重启后 agent 照旧记得。这个 service 把 DB + 磁盘 +
session 一起清干净,是 [[agents_chat_history.py]] 那个路由的实现层。

## 上下游关系

**被谁用**:`backend/routes/agents_chat_history.py` 的 `DELETE /{id}/history`,按
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
