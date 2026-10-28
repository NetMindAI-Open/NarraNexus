---
code_file: plugins/builtin.channels.wechat/src/narranexus_plugins/wechat_module/wechat_trigger.py
last_verified: 2026-09-09
stub: false
---

# wechat_trigger.py — iLink（企业微信）长驻 trigger

## 2026-09-09 — `disable_credential(credential, reason="")`（B-28 I1）

基类契约变更：以关键字 `reason=`（已脱敏截断）调用；这里透传给 manager 落到 `disabled_reason`，
并用 `log_disable_outcome` 对写失败打 ERROR。行为其余不变。

## 为什么存在

`ChannelTriggerBase` 的一个 channel 实现：长驻、多凭据、每条入站消息自己跑 AgentRuntime。
它在 `dedicated_trigger=True` 的那一类里（见 [[message_source_handler.py]]），这一点有两个
下游后果，都不是本文件自己实现的：`MessageBusTrigger` 不得重复派发它的频道，
[[local_bus.py]] 的未读谓词不得把它的历史行注入 agent 上下文。

## 这个渠道的特殊之处

**iLink 没有服务端历史 API。** 所以「我们自己留的记录**就是**历史」——
`WeChatContextBuilder` 把 [[inbox_recorder.py]] 写的 `inbox_thread_messages` 读回来当会话上下文
（2026-08-17 之前是 `bus_messages`）。这让记录层从「给人看的界面」变成了**同时是 operational
的**：写坏一条记录，下一轮 agent 就会把它当成自己上一句话读回去 —— 这正是
`(stayed silent)` 哨兵值必须从记录路径上摘掉的原因（见 [[channel_trigger_base.py]] 2026-08-18）。

**owner 认领。** `claim_owner_if_unclaimed` 让第一个说话的人成为凭据的 owner。参见
`WeChatCredentialManager`。

## Gotcha

- `get_conversation_history` 用 `row["message_id"]` 与平台 `message_id` 比较来剔除当前这一轮，
  而 `InboxRecorder` 写的是 `ibx_<uuid>`，所以这个比较**恒为假**。这是继承来的问题（旧写入器
  也用 uuid），已记在 todo 里；影响是当前消息可能在历史里出现一次。

## 2026-08-18 — 首次补写镜像（本 PR 只改了注释）

本文件此前无镜像（全仓 54 个源文件如此，见
`reference/self_notebook/todo/2026-08-18-mirror-coverage-gap.md`）。本 PR 对它的改动是纯注释
（把现在时的 `ChannelInboxWriter` 引用改成过去时、并指向 `InboxRecorder`），不构成行为变更。
