---
code_file: src/narranexus/platform/narrative/_narrative_impl/instance_handler.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（review r3 C1/I2）— 两套依赖判据的人群不相交；no-narrative 路径真正可达

- **可达性（C1）**：`handle_completion_no_narrative` 现在由 [[module_poller]] 发现查询的 narrative-less 半边
  驱动（此前 INNER JOIN link 使它不可达，下文各段「事件路径」的描述自本次起才成立）。
- **I2**：`_activate_resolved`（依赖 status 终态规则）只作用于**没有任何 narrative link** 的实例；两个调用方的
  候选都来自带 `_NO_NARRATIVE_LINK_SQL` 的查询——对账用 `get_blocked_page`，事件路径由 `get_by_agent(BLOCKED)`
  改为 `get_unlinked_blocked_by_agent`（下文 r2 段「保留 get_by_agent」已作废）。narrative 绑定实例只由
  `handle_completion` 的 link 规则（`_check_dependencies_from_db`）判，人群不相交，每个实例只有一套规则。
- 与 `handle_completion` 的收尾不对称（写进 docstring）：没有 link 可转 history；状态/`completed_at` 仅在与
  `new_status` 不同时才改写——JobTrigger 已写过终态和真实完成时间，消化历史积压时不改写。
锁：见 module_poller 同日段各用例 + 仓库 twin。

## 2026-09-10（review r2 I-A）— `reconcile_blocked_instances(blocked)` 只判调用方给的行

签名改为接收候选行：由 [[module_poller]] 传入它刚取到的一页 BLOCKED 行（每页 ≤ 200），本方法**不再**
`get_by_agent(BLOCKED)` 二次查询，所以调用方的页大小就是每次调用的真实上界（r1 docstring 那句
「caller bounds the batch」当时是假的——handler 自己又拉了全量）。传入行里 `agent_id` 不是本 handler
的、或快照状态已不是 BLOCKED 的，直接忽略。`handle_completion_no_narrative` 自己那次
`get_by_agent(BLOCKED)` **保留不变**——它的候选集语义不同（只要 `dependencies` 含刚完成实例的那些）。
锁：`test_reconcile_judges_only_the_rows_it_is_handed`（传空列表时即使表里有可激活行也返回 `[]`）。

## 2026-09-10（review r1 I10 + M3/M4/M5）— `reconcile_blocked_instances` 周期对账 + 激活尾巴只写一份

**为什么要对账**：依赖激活是纯边缘触发——只有 [[module_poller]] **看见**一次完成事件才会跑
`handle_completion_no_narrative`。两种情况让 BLOCKED 永久卡死：`/api/jobs/complex` 逐条建 job
（上游用 `TriggerConfig.immediate()` 立刻开跑），上游可能在下游那条 BLOCKED 行写入**之前**就完成，
完成扫描时找不到任何可激活的依赖方；以及任何丢失的完成事件（poller 重启、
`_process_completed_instance` 抛错）从不重放。B-16 之前这类 job 至少会（错误地）跑起来，之后它
彻底不跑且没人报告。新方法 `reconcile_blocked_instances()`：依赖全部终态
就激活；由 [[module_poller]] 每 15 分钟调用（候选集与分页见上节 r2 I-A）。BLOCKED 但 `dependencies` 为空的
实例**不动**只告警——那是本扫描解释不了的异常，不是该自动开跑的东西。

**判据与激活尾巴只有一份**（M3）：`_activate_resolved(blocked, repo, db)` 是「依赖全终态则激活」的
唯一实现，事件路径与对账都走它；`_activate_and_notify(instance_id, module_class, db)` 是
BLOCKED→ACTIVE + `module_registry.get(...).on_instance_activated` 的唯一尾巴，`handle_completion`
（narrative 路径）的那段 ~40 行重复也改成调它。事件路径与对账的差别只剩候选集：前者只看
`dependencies` 含刚完成实例的 BLOCKED（事件只说明这一件事），后者看调用方传入的那一页里属于本 agent 的 BLOCKED（r2 I-A）。
M4：依赖一次 `get_by_ids` 批量取，不再 N×M 次 `get_by_instance_id`。M5：状态读法统一为
`getattr(dep.status, "value", dep.status)`。

锁：`test_reconcile_activates_a_blocked_instance_whose_dependency_finished_unseen`、
`test_reconcile_leaves_a_blocked_instance_with_a_live_dependency`、
`test_reconcile_leaves_a_blocked_instance_with_no_dependencies`、
`test_reconcile_is_scoped_to_the_handlers_agent`、`test_both_paths_share_the_activation_hook`
（事件路径与对账都触发 `on_instance_activated`）。

## 2026-09-09 — B-16：新增 `handle_completion_no_narrative`（依赖解析不再强制要 narrative）

## 为什么存在

`InstanceHandler` 是 ModuleInstance 生命周期状态机的落脚点：一个 Instance
完成（COMPLETED/FAILED）之后，谁依赖它、依赖是否满足、要不要从 BLOCKED
拉到 ACTIVE，都在这里判定并写库（`module_instances` 状态 + 让对应 Module
的 `on_instance_activated` 钩子跑一次）。独立成一个 impl 文件是因为这段
状态转移逻辑既不是纯数据访问（要跨 instance 查依赖图、要回调 Module 钩子）
也不是编排层该管的细节（[[module_poller]] / hook_manager 只该说「这个
instance 完成了」，不该自己重写一遍依赖判定）。

## 这个文件不做什么

`handle_completion`（既有方法）**不是**通用的依赖解析器——它硬绑定
narrative：依赖方是谁、依赖是否满足，全部通过 `instance_narrative_links`
表按 `narrative_id` 查。一个从没绑定过 narrative 的 instance（比如
`/api/jobs/complex` 建的 job，那条 route 从不传 `narrative_id`）对它来说
**完全不存在**——不是「查到但不满足」，是从一开始就不在候选集合里。

## 上下游关系

- **被谁用**：[[module_poller]] 的 `_process_completed_instance`（Path B，
  唯一真正驱动依赖链前进的调用点）；hook_manager 的实时路径也调用过
  `handle_completion`，但 `narrative` 为空时直接跳过（那条路径的注释自己
  承认「durable guarantee lives in ModulePoller」）。
- **依赖谁**：`InstanceRepository`（读写 `module_instances`）、
  `InstanceNarrativeLinkRepository`（仅 `handle_completion` 用，
  `handle_completion_no_narrative` 完全不碰）、`module_registry`（拿到
  Module 类去调 `on_instance_activated`，激活后钩子——JobModule 的实现见
  [[job_module]]，只是把 `next_run_time`/`status` 拉活，不直接执行）。

## 设计决策

- **`handle_completion_no_narrative` 是新方法，不是改造 `handle_completion`
  兼容 `narrative_id=None`**：两者查询的数据源完全不同（`instance_narrative_links`
  vs `module_instances.dependencies` 直查），硬塞进一个方法会让分支逻辑
  比拆开还难读，且 `handle_completion` 现有调用方（hook_manager 实时路径）
  的行为必须原样保留——它们的失败模式、日志、runtime cache 更新
  （`narrative.active_instances` 那段）只对「确实有 narrative」的场景有意义。
- **依赖满足的判据是「终态」（COMPLETED 或 FAILED），不是「只认 COMPLETED」**：
  照抄 `handle_completion`/`_check_dependencies_from_db` 的既有语义——「是否
  block 在上游失败」是调用方的策略，本方法只回答「这条依赖走完了没有」。
- **按 `self.agent_id` 过滤 BLOCKED 候选**：`get_by_agent(status=BLOCKED)`
  一次性拉本 agent 下所有 BLOCKED instance 再逐个核对 `dependencies` 是否
  含刚完成的那个——依赖 instance_id 天然同批（同一个 `/api/jobs/complex`
  请求），不会跨 agent，跨 agent 的 BLOCKED instance 必须被忽略（测试
  `test_ignores_blocked_instances_belonging_to_a_different_agent` 钉死）。

## Gotcha / 边界情况

- **触发**：`/api/jobs/complex` 建的 job 从未绑定 narrative_id → **症状**：
  依赖链「永不触发」，下游 job 永远 BLOCKED（GitHub #114/#109）→ **根因**：
  唯一现存的依赖解析路径 `handle_completion` 要求实例先被 narrative 链接，
  这类 job 物理上做不到。修复见本方法 + [[module_poller]] 的分支。

## 相关约束

- 铁律 #8（改一处要 sweep 同类）：把这条依赖链修完整还需要
  [[job_repository]] 的 `update_next_run_time_by_instance` 纳入 `BLOCKED`
  状态、[[job_service]]/`instance_sync_service` 让 Job 自己的 `status`
  跟 ModuleInstance 的 `initial_status` 对齐——四处缺一不可，见各自 mirror md。
