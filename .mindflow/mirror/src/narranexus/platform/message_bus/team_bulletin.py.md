---
code_file: src/narranexus/platform/message_bus/team_bulletin.py
last_verified: 2026-08-18
stub: false
---

## 2026-08-17 — 团队房定位改用共享 helper

`db.get_one("bus_channels", {"created_by": team_<id>, ...})` 换成
[[team_rooms]] 的 `primary_room_of`。这条约定此前有四份实现，而最新的一份（job
的 origin 解析）落地时就已经和兄弟实现漂移了。本文件行为不变。

# team_bulletin — 公告栏的业务规则（预算 + agent 能写什么）

## 为什么在核心包，而不是挨着路由

**第一版把预算函数写在 `plugins/builtin.teams/src/narranexus_plugins/teams/routes.py`，核心包再反向 import 它** —— 核心包伸手进
web 层，架构明确禁止（API 层独立于核心包）。提交前修正：规则搬进核心包，路由 import 它。

这不是洁癖。两个面都要强制同一套上限：路由服务用户，MCP 工具服务 agent。
规则放在核心包，两边**由构造保证一致**；放在路由里，第二个调用方只能复制一遍，
然后两份慢慢漂移。

同时它是纯函数（不是 MCP handler、也不是路由体），照 [[team_files]] 的先例——
这些规则**就是**功能本身，应当能在不起工具服务器、不起 HTTP 客户端的情况下测。

## 拒绝，而不是截断

超预算一律拒绝并说明。悄悄截短比拒绝更糟：用户什么都没被告知，会继续以为整条规则生效，
而且**存活的那半句可能反转被砍掉那半句的含义**（「never share X with Y」砍在
「never share X」）。每条拒绝都点明撞到的是哪个上限和数字——只说「太长了」，
写的人（无论是人还是模型）只能猜要砍多少。

编辑路径的投影总数**减去被替换的文本**。若拿新文本去和仍包含旧文本的总数比，
就会拒掉那条正在缓解预算的编辑：告诉一个已经顶到上限的用户「先腾空间」，
而腾空间的唯一动作正是刚被拦下的那个。

## 权限是不对称的，这是方案 B 能成立的前提

agent 可以加，可以撤回**它自己写的**；绝不能碰用户的、别的 agent 的、或自动总结。

公告栏正是「约束 agent 的规则」所在之处，所以能删它们的 agent 就能删掉自己的缰绳——
而且不需要恶意，只需要一次听起来很合理的「整理一下」。用户对任何条目的一键删除是这笔
交易的另一半：**写权限之所以敢给，是因为它随时可撤。**

## team_id 来自服务端身份

不是工具参数。`bus_share_to_team` 让模型传 `team_id` 再三段校验；这里能用更强的方案，
因为 turn 本身已经知道自己在哪个团队。**于是「指认另一个团队」不是要防的攻击，而是不可表达的状态。**
私聊 turn 没有团队身份，所以直接拒绝，而不是静默落到某个团队上。

相关：[[team_bulletin_repository]]、[[teams]]（路由）、
[[_message_bus_mcp_tools]]（工具包装）、[[_mcp_identity]]（身份头）

## 2026-08-11 (review) — 通知搬进核心包，agent 写入也要落痕

`_post_bulletin_notice` 原本只挂在 `plugins/builtin.teams/src/narranexus_plugins/teams/routes.py` 的 4 个路由上，于是
**一条 agent 钉上去的规则改变了全队的行为，却在房间里不留任何痕迹**——验收标准写的是
「公告栏更新时群里出现系统消息」，而一半的写入方在跳过它。

现在是核心包里的 `post_bulletin_notice`，两个写入面共用（路由 import 它，不再自带一份）。
放核心包也是分层要求：核心包不能反向 import 路由。

房间不存在时直接返回，不建房间——公告栏的一次编辑不该凭空变出一个聊天频道。
写入被拒时不发通知：**宣告一个没发生的变更，比沉默更糟。**

## 2026-08-18 — owner 工具改名跟随

`send_message_to_user_directly` 拆成 `reply_owner`（回答刚说话的 owner）与 `notify_owner`
（未被问就主动告知）。两者行为相同但纪律相反，合成一个工具就要求模型每轮自己判断该用哪种
register。本文件里改到的是该 handler 注册的 `user_reply_tool_names` / 相关文案 —— 一两行，
但 registry 条目是**活的行为**：它决定哪些工具调用算作这个来源的一次回复，也是
`render_origin_declaration` 取 label 的同一条记录。规范解释见
[[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。
