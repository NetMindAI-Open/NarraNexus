---
code_file: plugins/builtin.job/src/narranexus_plugins/job_module/job_module.py
last_verified: 2026-09-04
---

## 2026-08-11 — create_mcp_server 去掉 db 工厂

`create_mcp_server` 从 `create_job_mcp_server(self.port, JobModule.get_mcp_db_client)`
改为 `create_job_mcp_server(self.port)`——job 工具全走 [[store]] AgentDataStore seam 取数据，
不再把 db 工厂线穿进 MCP 层。`JobModule.get_mcp_db_client`（继承自 base）本身保留，仅不再
被工具工厂消费。

## 2026-08-04 — instructions 教的 job_type 以 enum 为唯一真相

指令模板曾教一个 enum 里不存在的 `RECURRING` 类型（表格行 + 示例 +
`job_type` 取值行共三处）。越守 prompt 的模型死得越惨：提交
`job_type="recurring"` → `Invalid job_type` → 白烧一轮自纠（2026-08-04
W1 实测每次必现）。真相是 `SCHEDULED` 本身就是 cron/interval 周期任务
（schema/job_schema.py JobType 注释），RECURRING 行描述的正是它。现在
`tests/job_module/test_instructions_match_schema.py` 把模板教的 job_type
钉死在 enum 值集合上——双向断言（不教幻影类型、enum 值全部教到），
两个模板变体（legacy / STABLE）都覆盖。

同一张类型表的 ONE_OFF 行原写「run_at（或立即）」，但 service 对
ONE_OFF 强制要求 run_at，没有立即执行的缺省路径。现改成
`run_at (required)`，测试同时钉住必填描述，避免模型省略字段后白跑一轮纠错。

## 2026-07-28 — R4b："Current Job Status" 表搬进 contribute_turn_context

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

- 指令模板从 `__init__` 内联字符串**逐字节**上提为模块级常量
  `JOB_MODULE_INSTRUCTIONS`（提交时用 git-show 比对过字节一致）；
  `JOB_MODULE_INSTRUCTIONS_STABLE` 由两个 `.replace()` 推导：
  ① `{jobs_information}` 小节 → 静态指引句（jobs 表在本轮消息 turn context
  块里）；② "If there are jobs listed above:" → "listed in the turn
  context:"（方位词修正，非功能变更）。曾经内联在 __init__ 里的死代码
  `agent_id_note`（其 replace 早已注释掉）随上提一并移除。
- `contribute_instructions` override：按 relocation flag 选模板再走基类 format 路径；
  关 = legacy 逐字节一致。
- `contribute_turn_context`：`##### Current Job Status\n\n` +
  `ctx_data.jobs_information`（gather 填充逻辑不动；
  "*No jobs for this conversation.*" 空态行也随迁——搬迁不裁剪）。
  字段缺失 → ""（fail-open）。
- 动机：job 创建/完成/状态迁移使 `{jobs_information}` 会话中途变化
  （prod 稳定性 10/17），打穿 system prompt 前缀缓存。

# job_module.py — JobModule 实现

## 为什么存在

JobModule 是 AgentRuntime 侧的 Job 管理入口。它做三件事：在数据收集阶段把当前活跃 Job 的摘要注入系统提示（让 Agent 知道有哪些进行中的任务）；在执行后分析 Job 结果并更新状态；通过 MCP 工具暴露 Job CRUD 能力。

**Hook 实现**：实现了 `gather`（加载 Job 列表）和 `after_turn`（分析执行结果，CHAT 路径还更新 ONGOING Job 进度）。

**MCP 端口**：7803

**Instance 模型**：task module，每个 Job 任务对应一个 ModuleInstance，LLM 在实例决策时创建或复用。

## 上下游关系

- **被谁用**：`ModuleLoader` 通过实例决策加载（task module）；`HookManager` 调用两个 hook；同时被 `_job_lifecycle.py` 的函数调用（注入 `repo` 和 getter）
- **依赖谁**：`JobRepository`（DB 操作）；`_job_mcp_tools.create_job_mcp_server`（MCP 创建）；`_job_lifecycle.handle_job_execution_result` 和 `update_ongoing_jobs_from_chat`（hook 后处理委托）

## 设计决策

**用户过滤逻辑**：`gather` 里的 `_collect_jobs` 根据 `current_user_id` 过滤，只展示与当前用户相关的 Job（`related_entity_id == user_id` 或 `user_id == creator` 或无 related_entity_id）。这防止了销售经理看到自己针对其他客户的 Job 时，那些 Job 出现在客户的对话上下文里。

**关联任务注入（销售场景，跨模块数据流）**：`gather` 在格式化完用户自己的活跃 Job 表后，调用 `_inject_related_jobs_context`，把"以当前用户为目标对象、但不在其自有 Job 列表里"的销售任务追加成一个 *Related Tasks* 段落。数据流靠**顺序 hook + 共享 ctx_data**：SocialNetworkModule（capability 模块，在 active_instances 顺序里排在 JobModule 这个 task/虚拟实例之前）的 `gather` 先把 `entity.related_job_ids` 写进 `ctx_data.extra_data`；JobModule 随后读取、用 `_load_related_jobs_context` 载入 Job 详情、追加进 `jobs_information`，并把渲染文本回存到 `extra_data["related_jobs_context"]` 供 SocialNetworkModule 的 `after_turn` 做 persona 推断。**依赖前提**：data_gathering 默认 sequential 模式（`parallel_data_gathering=False`）——parallel 模式下每个模块拿 ctx_data 深拷贝、事后才 merge，这条 Social→Job 的 extra_data 交接会失效。无 `related_job_ids` 时该方法是 no-op，非销售轮次零开销。

**虚拟 JobModule 实例保证 MCP 工具可访问**：如果 LLM 决策没有选择任何 JobModule 实例，`ModuleLoader._ensure_job_module_available()` 会插入一个空 `instance_id` 的虚拟实例，保证 `job_create` 工具始终可用（否则 Agent 想创建 Job 但找不到工具）。虚拟实例的 `instance_id` 是空字符串，`after_turn` 里会忽略它。

**after_turn 的双路径**：JOB 触发 → `handle_job_execution_result` LLM 分析；CHAT 触发且有活跃 Job 实例 → `update_ongoing_jobs_from_chat` 检查 ONGOING 任务进度。两条路径互不干扰。

**`jobs_information` 的注入通道**：`gather` 填充 `ctx_data.jobs_information` 后，relocation flag 关闭时经 legacy 模板的 `{jobs_information}` 占位符进系统提示；开启时（默认）经 `contribute_turn_context()` 进当前轮消息的 turn context 块（见上方 2026-07-28 记录）。这是 JobModule 与 prompt 集成的唯一数据源。

## Gotcha / 边界情况

- **Job 状态更新的双入口竞争**：ONGOING Job 的状态由 `after_turn`（入口 1，LLM 分析）和 `job_trigger._finalize_job_execution`（入口 2，机械更新）两处更新。入口 2 有"状态仍为 RUNNING 时才机械更新"的保护，但如果入口 1 的 LLM 调用比入口 2 慢，可能出现竞争窗口，详见 `job_trigger.py` 的注释。
- **`instance_ids` 里以 `job_` 前缀判断活跃 Job 实例**：`after_turn` 通过 `[inst for inst in instance_ids if inst.startswith("job_")]` 收集活跃 Job。虚拟实例（空字符串）被跳过。

## 新人易踩的坑

- `instance_id`（Module 实例 ID，`job_xxxxxxxx`）和 `job_id`（Job 记录 ID，`job_xxxxxxxx` 但是不同的8位随机数）是两个不同的 ID，通过 `instance_jobs` 表的 `instance_id` 字段关联。混淆这两个 ID 会导致查询结果为空。

## 2026-08-19 — instructions/摘要接 end_at

`JOB_MODULE_INSTRUCTIONS` 的 SCHEDULED 行、ONGOING 详情 bullet 与"How to
Create a Job"第 4 节新增可选 `trigger_config.end_at` 教学（对 scheduled 与
ongoing 都有效、one_off 无效；用户给有界时长就用它，别依赖自我暂停）；
`_format_job_row` 的 trigger 列带 `until {end_at}`——没有它，agent（比如
onboarding 引导判断告别日）只能从 payload 文本里抠自己的到期日。注意新增
行都不含 `job_type` 反引号字样，test_instructions_match_schema 的 token
抓取不受影响。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.

## 2026-09-04 · declares its platform metadata (batch 5b)

`get_config()` is static and carries what the platform used to table about this module (default / base / always-load membership, instance prefix, role, display, decision metadata as applicable).

## 2026-09-04 · declares role "jobs" and reschedules on activation (batch 5b.2)

`on_instance_activated` sets the job's next run time (moved from the narrative instance handler's `== "JobModule"` branch); `always_available_tools=True` keeps job tools reachable through the loader's generic virtual-instance rule.
