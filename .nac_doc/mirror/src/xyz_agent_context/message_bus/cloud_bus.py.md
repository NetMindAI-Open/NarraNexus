---
code_file: src/xyz_agent_context/message_bus/cloud_bus.py
last_verified: 2026-04-10
stub: false
---

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
