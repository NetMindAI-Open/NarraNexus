---
code_file: src/narranexus/platform/message_bus/cloud_bus.py
last_verified: 2026-09-09
stub: false
---
## 2026-09-09（review M1）— `get_message` 跟随协议（仍 NotImplementedError）

## 2026-09-09 — 签名跟随契约：keyword-only `part_index/part_count`

仍 `NotImplementedError`；只是让 `test_team_message_segments` 的「实现不得偏离协议」断言
继续成立。

## 2026-08-14 — 签名跟随契约：`segments` + `get_messages_before`

两处都只是 `NotImplementedError` 的桩，但**必须跟着抽象基类走**：这个文件存在的意义就是
"云端实现还没写"这件事是显式的、有类型的。签名漂移会让它在真正实现的那天，从一个明确的
待办变成一个隐蔽的不兼容。

## 2026-08-07 — 签名跟随抽象契约增加 root_run_id

仍然 NotImplementedError;签名一致是为了 cloud 落地时不必再改一遍协议层。

## 2026-08-03 — both send stubs carry sender_turn_source

与 ABC 的新参数保持同步(见 [[message_bus_service]]);仍 raise
NotImplementedError。修复前传这个 keyword 会先 TypeError,盖掉真正的
"not yet implemented" 信号。

## 2026-07-31 — send_message stub carries event_id

Signature kept in sync with the ABC's new `event_id` param (see
[[message_bus_service]]); still raises NotImplementedError.

## 2026-07-22 — stub surface re-synced with the ABC

Added `NotImplementedError` stubs for `get_recent_messages` (new in the ABC,
see [[message_bus_service]]) plus three older ABC methods that had never been
stubbed here (`get_channel_members` / `kick_member` / `get_agent_profile`) —
without them the class had silently become un-instantiable (abstract-method
TypeError), defeating its placeholder purpose.

# cloud_bus.py — 云端 MessageBus 实现占位

## 为什么存在

`MessageBusService` 接口为未来迁移到云端消息队列（Redis Pub/Sub、AWS SQS、自建 REST API 等）预留了实现槽位。`CloudMessageBus` 是这个槽位的占位符，使得系统代码里可以出现 `CloudMessageBus` 的类型引用而不报 `NameError`，即使实现还未完成。

它的存在也是对架构意图的文档性说明：MessageBus 设计之初就考虑到了云端扩展，不是只有一种 SQLite 的本地实现。

## 上下游关系

**被谁用**：目前没有任何生产代码使用 `CloudMessageBus`，所有代码都用 `LocalMessageBus`。

**依赖谁**：继承 `MessageBusService` 抽象接口。

## 设计决策

所有方法直接 `raise NotImplementedError`，不提供任何局部实现。这比提供错误的"伪实现"更安全——如果有人意外实例化了 `CloudMessageBus`，第一次调用就会明确报错，而不是静默返回错误数据。

`__init__` 接受 `api_base_url` 和 `auth_token` 两个参数，定义了未来实现的基本接口契约——云端实现至少需要这两个配置项。

## 新人易踩的坑

这是一个占位文件。在它真正实现之前，任何想在生产环境使用云端 MessageBus 的尝试都会在第一次方法调用时崩溃。确认当前系统用的是 `LocalMessageBus`，不要期望 `CloudMessageBus` 可用。

## 2026-08-11 — 两个新方法的 NotImplementedError 占位

`ack_read` / `count_unread` 随协议加入。云实现整体仍未落地,占位与同文件其余方法一致。

## 2026-08-12 — `send_message` 跟随协议加 `routed_by`

仍是 NotImplementedError 占位。

## 2026-08-12 — `has_unread_before` 占位

随协议加入,仍是 NotImplementedError。

## 2026-08-14 — 跟进 `has_message_from_turn`

随协议新增一个 `NotImplementedError` 占位,与本文件其余方法同形。这份实现始终是
"协议长什么样"的镜子,不是可用后端。

## 2026-08-14 (补) — `send_to_agent` 跟进签名

同上,占位实现随协议同步。

## 2026-08-23(补)— get_pending_messages 签名加 channel_id

`get_pending_messages` 签名加 `channel_id: Optional[str]=None` 与抽象契约([[message_bus_service.py]])/[[local_bus.py]] 同步。
本类仍是 NotImplemented stub(云端 bus 未实现),仅保持签名不漂移。
