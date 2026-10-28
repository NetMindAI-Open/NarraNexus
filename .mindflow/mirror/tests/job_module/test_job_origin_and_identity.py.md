---
code_file: tests/job_module/test_job_origin_and_identity.py
last_verified: 2026-08-17
stub: false
---

## 2026-08-17 — 为什么存在

两个缺陷同一个根：**job 记得做什么，忘了在哪被问**。

**回报面**：`job_module` 只注册了一个回复工具，执行提示词又写死「owner 打开这
个会话时会看到」。于是在团队房里当着四个人要的「明早提醒我们」投进了一个人的
私聊，问的那个房间再没收到过回音。

**身份**：bus 轮次以 `user_id = sender_agent_id` 跑（[[message_bus_trigger]]），
那个值一路流到 `job.user_id`，于是 job 被登记在 `usr_<uid>` 或对端 agent_id 名下
——一个不存在的 owner。owner 的 Jobs 列表永远空着，而 agent 报告成功。

## 为什么身份修在写入点而不是身份层

[[_mcp_identity]] 的 `resolve_caller_user_id` 只覆盖占位符，注释写明多用户流里
「看似真实但不匹配」的值可能合法——那是关于**通用身份策略**的判断，本文件不去
动它。「这个 job 是谁的」是更窄、且库里有答案的问题（`agents.created_by`），所
以断言打在 `create_job_from_args` 上：那是本地 MCP 进程与云端 seam 路由**共用**
的写入路径，修在更上层会漏掉其中一个调用方。

## 回归的那一半同样重要

私聊 job 必须逐字不变（PRD 验收 #8），所以「无 origin 时不往任何房间投」「空
输出仍保留 owner inbox 的运维样板」都是断言，不是顺带。
`test_an_empty_run_does_not_put_a_metadata_block_in_the_room` 走
`_run_agent` 而不是 `_deliver_to_origin`，因为它测的是**哪段文本进了房间**——
两条语句的先后顺序，不是投递本身。

## 提示词与投递必须同源

`test_a_room_job_is_told_its_reply_goes_to_the_room` 钉的是两者读同一个字段
（`jobs.origin_source`）。两个方向都会坏：房间来源的 job 去调
`notify_owner` 投错地方；私聊 job 以为明文自动上墙则**整个丢
掉**答案。

## 关联

上游：[[job_trigger]]、[[_job_writes]]、[[_job_mcp_tools]]、[[prompts]]。
需求出处：飞书 PRD《异步协作》第三层（验收 #4/#5/#8）。
