---
code_file: src/narranexus/platform/message_bus/__init__.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 包 import 不再注册总线的消息来源

顶层那段 `MessageSourceRegistry.register(MessageSourceHandler(name="message_bus", ...))`
（外裹 `except ValueError: pass`）已删。它的实测后果是：只 import
`narranexus.platform.agent_framework` 就会打出「registered handler for 'message_bus'」，
而此时每一个内核 slot 都还是空的——一张与插件平台完全平行、无人可禁用的表。

这条来源现在由拥有总线模块的插件 `builtin.message_bus` 在自己的 `contribution.py` 里以
`MessageSourceSpec` 提供，进 `ingress.message_sources`。字段语义（`message_agent` /
`message_team` / `notify_owner` 都算送达，但只有 `notify_owner` 是 owner 可见）原样搬过
去，注释也跟着搬——那正是 2026-08-01 把真实总线送达记成 NO-REPLY、污染了兜底决策所依据
的指标的那条教训。

## 2026-08-04 (review 三) — 名单的活消费方补齐

round 2 抓到扩容名单一度没有任何活消费方（度量仍失真）。现在
`user_reply_tool_names` 的消费方 = ChatModule._origin_delivered_text
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
  `read_history` 等非投递工具不含 send 名单子串，不会误判。**这条不是自明的**：
  子串匹配意味着只要有真实工具名包含 `message_agent` / `message_team` /
  `notify_owner`，每个沉默轮次都会被记为已投递、污染 fallback 决策所读的
  no-reply 指标。`test_bus_reply_judgment.py` 现在从注册处枚举全部工具逐个断言，
  而不是像原先那样断言一个已退役的名字（那等于什么都没断言）。

## 2026-08-13 — 注释指向更新

~~`_delivered_to_origin` 现在只是 `bool(_origin_delivered_text(...))`~~ —— 2026-08-14 它已被删除(生产零调用方),真正做抽取的是
后者,注释随之改指。

## 2026-08-18 — owner/bus 工具改名（新增条目，不改写上面的历史）

`user_reply_tool_names` 现为 `notify_owner` + `message_team` + `message_agent`。
上面 2026-08-04 那条记的是当天的名字（`send_message_to_user_directly` /
`bus_send_message` / `bus_send_to_agent`），**故意保持原样** —— 我一度把那条里的名字改成了
今天的，那是在销毁记录：镜像的价值就在于它记的是当时发生了什么，带日期的条目里改名会让
「什么时候变的、从什么变的」不可考。改名一律新增条目。

对应关系：`send_message_to_user_directly` 拆成 `reply_owner`（回答刚说话的 owner）与
`notify_owner`（未被问就主动告知）；`bus_send_message` → `message_team`；
`bus_send_to_agent` → `message_agent`。本文件注册的是 message_bus 来源的判定名单，
所以只出现 `notify_owner`（bus 轮次可以用「告诉 owner」来应答）。

`row_prefix_template` 同批从 `[Bus · from agent={from_agent}]` 改为
`[private message from {from_agent}]` —— 「Bus」是传输层的名字，而其余每个 handler 的前缀
命名的都是消息**来自哪里**。
