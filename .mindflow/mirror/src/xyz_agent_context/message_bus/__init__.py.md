---
code_file: src/xyz_agent_context/message_bus/__init__.py
last_verified: 2026-08-04
stub: false
---

## 2026-08-04 (review 三) — 名单的活消费方补齐

round 2 抓到扩容名单一度没有任何活消费方（度量仍失真）。现在
`user_reply_tool_names` 的消费方 = ChatModule._delivered_to_origin
（[DELIVERED-BG]/[NO-REPLY-BG] 持久化二分，即兜底决策的度量口径）；
owner-visible 子集继续服务锚点与历史可见性。注释同步改口。

## 2026-08-04 (review 修正) — handler 增加 owner_visible 子集

`owner_visible_reply_tool_names=("send_message_to_user_directly",)`：
bus 交付名单（含 bus 工具）只服务「是否回复了来源」；owner 会话锚点与
chat 历史持久化只认 owner-notify 工具。见 [[message_source_handler]]。

# message_bus/__init__.py — 包出口 + message_bus 来源 handler 注册

## 为什么存在

除 re-export 外，import 时注册 message_bus 的 MessageSourceHandler：
判定「这轮算不算回复了用户」的 reply 工具名单 + bus 行前缀模板。

## 2026-08-04 — 判定名单补 bus 投递工具

`user_reply_tool_names` 从仅 send_message_to_user_directly 扩为
+ `bus_send_message` + `bus_send_to_agent`。旧名单把真调了 bus 送达的
run 也记 NO-REPLY（8/1 实锤，如 Maestro run_1994fd41）——既错标运行，
也污染「bus agent 到底多常不交付」的度量（该指标决定要不要加平台侧
兜底，Owner 2026-08-04 拍板：先测量再定兜底）。与 [[message_bus_module]]
的 expressive 声明保持同一份工具集（声明面/判定面镜像对齐）。

## Gotcha

- 判定是子串匹配（`pattern in tool_name`），mcp__ 前缀形态自然命中；
  bus_get_messages 等非投递工具不含 send 名单子串，不会误判。
