---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/__init__.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 包 import 不再注册 job 的消息来源

删掉顶层的 `try: MessageSourceRegistry.register(MessageSourceHandler(name="job", ...))
except ValueError: pass`。反向的失败同样真实：某个角色下如果只 import 了 job 的子模块而
没 import 这个包，`job` 的 handler 就不存在，`chat_module` 会把定时任务的回复当成普通聊
天行——一个只在特定角色/发行版下出现、没有任何日志的行为差异。

这条来源搬到 `contribution.py` 的 `MESSAGE_SOURCES`，走 `ingress.message_sources` 位，由
manifest 声明。

# job_module/__init__.py — package surface

除 `JobModule` / `JobInstanceService` 与 job channel handler 注册外，re-export
[[_job_reads]] 的读 helper（PR-8）与 [[_job_writes]] 的全部写 helper——
`update_job_from_args`（PR-8b）+ `create_job_from_args` / `pause_job_from_args` /
`cancel_job_from_args`（2026-08-11，job 三写迁 seam）——供 AgentDataStore seam 的
[[store]] DirectStore、agent-scoped [[agents/jobs]] 路由、以及前端 [[jobs]] 路由
import **包**、不 reach 私有叶子（同 social/basic_info 先例）。


## 2026-08-18 — owner 工具改名跟随

`send_message_to_user_directly` 拆成 `reply_owner`（回答刚说话的 owner）与 `notify_owner`
（未被问就主动告知）。两者行为相同但纪律相反，合成一个工具就要求模型每轮自己判断该用哪种
register。本文件里改到的是该 handler 注册的 `user_reply_tool_names` / 相关文案 —— 一两行，
但 registry 条目是**活的行为**：它决定哪些工具调用算作这个来源的一次回复，也是
`render_origin_declaration` 取 label 的同一条记录。规范解释见
[[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。
