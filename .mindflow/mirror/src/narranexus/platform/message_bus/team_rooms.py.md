---
code_file: src/narranexus/platform/message_bus/team_rooms.py
last_verified: 2026-08-18
stub: false
---

# team_rooms.py — 「团队房在哪」的唯一答案

## 为什么存在

「团队房 = `created_by` 为 `team_<id>` 标记的那个 group 频道」是个**约定**，不是
定律，而它长出了四份独立实现：工作板工具、公告通知、teams 路由，以及最新的 job
origin 解析。四份拷贝意味着约定要改那天有三次漏改的机会——给一个团队开第二个房
间、或者加个 `is_primary` 标记，漏掉哪份，哪份就解析到跟产品其余部分不同的房间。

促成抽取的是：**最新那份落地时就已经漂移了**——它丢掉了兄弟实现的
`bus_agent_activity` 兜底分支，于是同一个团队在工作板工具和 job 面会解析出不同
结果。

## 没有被折进来的那一处

`plugins/builtin.teams/src/narranexus_plugins/teams/routes.py` 用一条 `created_by IN (...)` 一次解析**多个**团队的房
间。它看着像第五份拷贝，其实不是：它回答的是另一个问题，用这个 helper 重写会把
一次索引查询变成 N 次——正是 repository 层存在的意义所要避免的 N+1。它共享**约
定**，不共享代码。

## 契约

返回 `None` 而不是空串：三个调用方都拿它当写入目标，半个答案会把行写到 `""`。
房间还没建是正常状态而不是错误（房间是懒创建的）。永不抛——三个调用方对「没有房
间」都是安静地放弃（没有板子 / 不发公告 / 没有 job origin），拿到异常也做不了别的。

## 上下游

被用：[[_work_board_mcp_tools]]（注入身份分支）、[[team_bulletin]]、
[[_job_mcp_tools]]。共享约定但不共享代码：`plugins/builtin.teams/src/narranexus_plugins/teams/routes.py`。

## 2026-08-18 — 与 dev 的 team_rooms 合并成一个联合体

本分支与 dev 的 PR #310 各自新建了同名文件、内容不相交：dev 侧只有 `primary_room_of`，
分支侧有 `team_room_marker` / `get_or_create_team_room` / `resolve_team_room` / `room_roster`。
任一侧「取我的」都会在容器启动时炸 ImportError（取分支侧则 `errand.py` 找不到入口，取 dev 侧
则 `message_team` 找不到房间解析），所以只有联合是对的。

联合后 `resolve_team_room` 与 `primary_room_of` 是同一个查询的两个名字，已合并为
`primary_room_of` 一个 —— 它的 docstring 现在自己写明「唯一读路径」，因为四份副本就是从
「两个名字一个查询」开始的。
