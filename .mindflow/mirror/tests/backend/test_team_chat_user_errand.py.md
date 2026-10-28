---
code_file: tests/backend/test_team_chat_user_errand.py
last_verified: 2026-08-17
stub: false
---

## 2026-08-17 — 为什么存在

[[errand]] 挂在 [[message_bus_trigger]] 上，那是 **agent 回帖**走的路。人发的消
息从 `plugins/builtin.teams/src/narranexus_plugins/teams/routes.py` 直接进 bus，于是「@Bruno 把数拉一下」被无视之后
**什么痕迹都没有**：板子上没有行，巡查无事可扫，闭环率报告里也没有它。

而这恰恰是**用户唯一亲眼看得见**的那类断链——agent 之间互相无视用户看不见，自己
被无视看得见。

顺带修的是分母：`make work-item-report` 要回答「有多少交接没回来」，只量 agent
→agent 那一半，回答的是另一个问题。

## 三条边界

- 无 @ → 路由到默认响应者，那是**平台挑人回答**，不是用户派活，不开项；
- `@all` → 房间级招呼，没有人「迟到」，不开项；
- 记账失败绝不能让用户的消息发送失败——消息已经在房间里了，让用户重打一遍所有
  人都已经看见的东西是最差的结果。

走 HTTP 真驱动而不是断言源码文本，理由同 [[test_team_chat_paging]]：源码断言在改
名时变红、在行为回归时变绿，两头都反了。
