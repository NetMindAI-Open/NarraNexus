---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/_job_mcp_tools.py
last_verified: 2026-09-07
---

## 2026-09-07 — 私有平台模块换成公开门面（批 6c，A2-1）

本文件曾 import `narranexus.platform` 的下划线私有模块。批 6c 在拥有它的包上开出了
具名公开函数（`module_system` 的 caller-identity 解析器 / `marketplace` 的
store·pipeline·secret-box / `agent_framework.llm.prompt_probe_emit` /
`agent_framework.adapters.build_tool_policy_guard`），本文件改用它们。
理由不是命名规范：这些 MCP 工具和 helper 自批 6b 起是**独立的 wheel**，
「包内私有」对它们已经不成立了。`pyproject.toml` 的
`plugins never import a private platform module` 契约（只查直接 import）守住这条线。

## 2026-08-17 — 复用 helper + 日志级别提到 warning

自带的那份团队房定位换成 [[team_rooms]] 的 `primary_room_of`——它落地时就丢了兄
弟实现的兜底分支，两份实现行为已经不一致。

`except` 从 `debug` 提到 `warning`：这个分支正是未来「mcp 容器不再持有 DB 凭据」
那次迁移会撞上的地方，而症状是**静默的**——room origin 消失，job 悄悄退回 owner
私聊，也就是这个功能刚修好的那个 bug 原样回来。翻开关的人需要看得见。

`caller_team_id_from_request()` 为空**仍然静默**：那是私聊/peer DM 的正常路径，
在那儿告警会让每个 owner-chat job 都响一次，然后所有人学会忽略真正要紧的那行。

## 2026-08-14 — `_caller_job_origin`：来源只能来自注入身份

`job_create` 记录这一轮**跑在哪个团队房**。取自服务器注入的 team 身份，**绝不做
成工具参数**：模型被问「你在哪个房间」只能猜，而猜错的 channel id 会把别人的提
醒投进别人的房间。与工作板工具对 team_id 的处理同源
（[[_work_board_mcp_tools]]）。

**没有新增 bearer 字段**：bearer 的字段表是位置固定且冻结的，追加合法但要动协议
和每个读者；而团队房**确定性地**就是 `created_by` 为 `team_<id>` 标记的那个群频
道，这个事实已经在线上了。

失败返回 `(None, None)`：报给 owner 是旧行为，而旧行为永远不是「我们没搞清它从
哪来」的错误答案。

## 2026-08-11 — create/pause/cancel 迁走 seam，工具彻底弃 db 凭据

job_create/pause/cancel 改为 `get_agent_data_store().job_create|job_pause|job_cancel(...)`，
实现下沉到 [[_job_writes]] 三个 `*_from_args`（DirectStore 本地 / HttpStore 调 [[jobs]]
孪生路由）。**`create_job_mcp_server(port)` 去掉 `get_db_client_fn` 参数**（读工具早已走 seam，
写工具迁完后它彻底无用），[[job_module]] 的 `create_mcp_server` 同步改。`setup_mcp_llm_context`/
`LLMConfigNotConfigured`/`JobRepository`/`loguru.logger` import 随之全删（W1 结构化错误兜底搬进
`create_job_from_args`）。至此 **job 模块 mcp 可达代码 `get_mcp_db_client()` 残留 = 0**，
配合 lark/narra 零凭据，mcp 容器可 strip `DB_PASSWORD`。cross-agent 现读作 not found。

## 2026-08-10 (PR-8b) — job_update 迁走 seam

job_update 改为 `get_agent_data_store().job_update(agent_id, job_id, fields)`，~90 行
build-updates 逻辑下沉到 [[_job_writes]]（DirectStore/agent 路由/前端 jobs 路由同源）。
cross-agent 现读作 not found。job_create/pause/cancel 本 PR 不迁（create 用 LLM）。

## 2026-08-10 (PR-8) — 三个读工具迁 AgentDataStore seam

job_retrieval_by_id/_semantic/_by_keywords 改为 `get_agent_data_store().job_retrieval_*`，
数据访问下沉到 [[_job_reads]]（DirectStore 本地 / HttpStore 调 [[jobs]] 孪生路由）。semantic
里的 `setup_mcp_llm_context` 删除（search_keyword 是 BM25 不用 LLM）；import 仍被写工具
（create/update/pause/cancel）使用。写工具本 PR 不迁（PR-8b）。


## 2026-08-10 — job_update: effective_type 提前解析(修 type-switch 僵尸 job)

`job_update` 里 `effective_type` 原在 trigger_config 分支中 `updates.get(
"job_type", job.job_type)` 读——但 job_type 分支跑在其**后**,永远拿旧类型。
one_off→scheduled + 新 cron 同时提交时 compute_next_run 用旧类型算出 None,
next_run_time 被写空,job 变成 status 正常却永不被调度的僵尸。改为进 updates
前先解析 effective_type;backend `/api/jobs/{id}` 路由同处 lockstep 一起改
(两边对称同一 bug)。纯实现重排,工具对 LLM 的 schema/语义不变。


## 2026-08-04 — job_create 补齐兄弟工具的边界纪律（W1）

- **job_create 原是全文件唯一裸奔的工具**：其余 6 个都整体 try/except，
  它的 `setup_mcp_llm_context(agent_id)` 直接把 `LLMConfigNotConfigured`
  的原始异常文本甩给模型——模型读成「做不了」并如此告诉用户（prod 报障
  的一条真根因）。现在同样整体包裹；`LLMConfigNotConfigured` 单独接住，
  文案指回「用 instructions 里的真实 Agent ID，别用占位符」——这是模型
  唯一能自愈的失败，值得专门的话术；其余异常与兄弟工具同形
  `{success: False, error: str(e)}`。
- **`trigger_config` 的公开 schema 由 `TriggerConfigArg`（TypedDict）生成**。
  `NotRequired` 让嵌套字段保持普通类型；FastMCP 默认把命名 TypedDict 发布为
  `$defs/$ref`，而 job_update 的 Optional 还会包一层 `anyOf[$ref,null]`，
  会被 schema 严格的 provider 拒绝。现在用 `Annotated + WithJsonSchema`
  发布内联 `type: object + properties`，无 `$defs`、`$ref`、`anyOf`；
  job_update 还移除与非 null object 类型矛盾的 `default: null`。TypedDict 的
  docstring 是模型每轮都能看到的 description，因此只保留简短字段提示，工程
  原因留在代码注释和本文。
- **运行时刻意接收普通 object，而非让 TypedDict 抢先校验**：字符串化 JSON
  仍由 FastMCP 的 object 边界拒绝；object 内部的必填字段和深层规则统一交给
  `schema.TriggerConfig`。这样缺 timezone 等错误会进入工具 try/except，返回
  `{success: False, error: "Invalid trigger_config: ..."}`，不会在函数体外泄漏
  FastMCP `ToolError`。公开 schema 仍要求 timezone，运行时和模型契约没有放宽。
- 测试：`tests/job_module/test_job_mcp_tool_hardening.py`（直接检查参数原始
  schema 是内联 object 且无 `$defs/$ref/anyOf/default:null`；description 保持
  模型友好且短小；可选字段平铺类型；缺 timezone 等异常回结构化 error）。

# _job_mcp_tools.py — JobModule MCP 工具定义

## 为什么存在

从 `job_module.py` 分离出来（2026-03-06 重构），把 MCP 工具注册逻辑与 Module 的 Hook 生命周期解耦。`job_module.py` 专注于数据收集和执行后处理，这个文件专注于 Agent 如何通过 MCP 工具管理 Job。

提供 7 个工具：`job_create`、`job_retrieval_semantic`、`job_retrieval_by_id`、`job_retrieval_by_keywords`、`job_update`、`job_pause`、`job_cancel`。

## 上下游关系

- **被谁用**：`JobModule.create_mcp_server()` 调用 `create_job_mcp_server(port)`（无 db 工厂——每个工具经 seam 取数据）；`ModuleRunner` 部署返回的 FastMCP 实例；`JobModule.get_instance_object_candidates()` 通过 `fastmcp.Client` 内存调用 `job_retrieval_semantic`
- **依赖谁**：[[store]] 的 `get_agent_data_store()`（唯一数据入口，DirectStore 本地 / HttpStore 云端）。DB 操作（JobRepository/JobInstanceService）、embedding、setup_mcp_llm_context 全下沉到 [[_job_reads]]/[[_job_writes]] 的共享 helper——本文件不再直接 import 任何 repo/service。

## `agent_id` 如何传入

所有工具都要求显式传入 `agent_id` 和 `user_id`。MCP 工具在独立进程里没有"当前 Agent 上下文"，必须由 LLM 从系统提示里读取并传入。`JobModule.__init__` 里的 instructions 包含 `Your agent_id is {agent_id}` 提示，让 LLM 知道该用哪个值。

## 设计决策

**`job_create` 的强防重创建约束**：工具 docstring 首句即幂等规则——先查 "Jobs I Just Created" 再创建（2026-07-24 压缩前是大段 "CHECK IF I ALREADY CREATED JOBS FIRST" 警告 + 何时用/不用清单，压缩后保留规则本身）。这是因为 LLM 在接收用户"多步骤"请求时容易重复创建 Job（见实例决策提示词里也有同样的 WARNING）。

**`depends_on_job_ids` vs `dependencies`**：工具参数用 `depends_on_job_ids`（实例 ID 列表），内部转为 `dependencies`（DB 字段）。这个命名隔离是为了让 LLM 传入的是 job 的 `instance_id`（如 `job_a1b2c3d4`），而不是 `job_id`（DB 主键）。两者都是 8 位随机后缀格式，容易混淆。

**语义检索的向量**：`job_create` 时调用 `get_embedding()` 生成向量存入 DB，`job_retrieval_semantic` 时对查询文本也生成向量做余弦相似度检索。向量生成失败时 `job_create` 不会中断（向量字段可以为空，但语义检索功能会失效）。

## Gotcha / 边界情况

- **`related_entity_id` 的语义**：如果 Job 是"Agent 为自己做的事并向请求者汇报"，`related_entity_id` 填请求者的 `user_id`；如果 Job 是"针对另一个用户的行动（如销售跟进）"，填目标用户的 `user_id`。这个区分决定了 JobTrigger 执行时加载哪个用户的上下文，非常关键但容易搞错。

## 新人易踩的坑

- `job_cancel` 会把 Job 标记为 `CANCELLED` 并同时把 `module_instances` 里的实例标记为 `completed`（触发 ModulePoller 的依赖链）。取消一个 Job 可能意外激活等待该 Job 的下游 Job。

## 2026-07-24

**`job_create` / `job_update` docstring 压缩（token 优化 W3）**：这两个 docstring 就是 LLM 每次调用都要读的工具 schema（原合计约 12K 字符，占全部工具 schema 的 ~16%），压到合计 3,998 字符（job_create 5,417→2,130，job_update 6,677→1,868）。只改 docstring，零代码/签名改动。

刻意保留的（弱模型如 DeepSeek 依赖这些）：每个参数的语义、trigger_config 三种 shape 的 cron/interval 格式、timezone 必填且为 IANA 名、`run_at` 必须 naive ISO 8601（禁 "Z"/offset，这是最常见的构造错误）、job_create 的防重创建规则（先查 "Jobs I Just Created"）、`related_entity_id` 的两分支规则（决定执行时加载谁的上下文）、`depends_on_job_ids` 传 instance_id 而非 DB job_id、`guidance_text` 追加 vs `payload` 整体替换、`next_run_time` 与 frozen timezone 的关系、status 三值及 cancelled 的终态性；每个工具各留一个紧凑示例 + Common errors 段。

刻意删除的：重复的销售场景长示例（原 job_create 3 个、job_update 6 个）、"WHEN TO USE" 叙事段、两个工具间重复的背景说明（job_update 的 trigger_config 完整 JSON 示例改为引用 job_create 的 shape 并压成单段速记）、"Feature 2.2.2 / Type-B" 内部黑话。原 docstring 中 `notification_method: default "inbox"` 与签名默认值 `"direct"` 矛盾，压缩版不再陈述默认值（schema 自带），顺手消除了这个误导。

其余 5 个工具本次未动（`job_retrieval_semantic` 1,572 / `job_retrieval_by_id` 516 / `job_retrieval_by_keywords` 822 / `job_pause` 589 / `job_cancel` 716 字符，raw 计）。

## 2026-08-19 — TriggerConfigArg 增 end_at

发布给模型的 trigger_config schema 增 `end_at: NotRequired[str]`（naive 本地
ISO，run_at 同款约定），job_create/job_update docstring 的 scheduled **与
ongoing** shape 都教了它（三轮 review：TriggerConfigArg 是全类型共享的一份
schema，模型看到的是"键存在"而非"只对 scheduled 有效"，所以平台侧把
ongoing 也接上了地平线、one_off 显式忽略——语义必须一句话说得清）——没有这一步，模型在协议层就无法表达"这个日程排到哪天为止"，
"接下来两周每天提醒我"只能建无限期 job + 让模型记得自己暂停（已被证明会被
finalize 回滚的机制）。沿用 NotRequired 而非 Optional（见上方 $ref/anyOf-
null 说明）；真校验仍在 TriggerConfig（naive validator 的报错足够模型自纠）。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.

Fix 2026-09-07: job_create/job_update docs state that cron/run_at are wall-clock times in the given timezone and must never be converted to UTC (a "9am Beijing" request had been stored as `0 1 * * *` + Asia/Shanghai).
