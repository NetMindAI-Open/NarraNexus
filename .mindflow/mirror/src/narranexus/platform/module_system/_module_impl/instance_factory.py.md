---
code_file: src/narranexus/platform/module_system/_module_impl/instance_factory.py
last_verified: 2026-09-04
stub: false
---

## 2026-08-05 — `_register_agent_in_bus` 改为走同伴发现的单点 seam（review）

原来这个方法自己手写一条 `bus_agent_registry` 的 upsert：`capabilities` 硬编码
`json.dumps([])`，description 规则是 [[agent_discovery_sync]] 的手抄版（还漏了
占位符判断），连 `registered_at` / `last_seen_at` 的时间格式都自己算 —— 也就是
P1 section 02 那个「A2A 发现全空」缺陷的**原始形态**，藏在第三个文件里。函数体
换成 `await sync_agent_discovery(self._db, agent_id)`，顺带去掉函数体内的
`import json` / `from datetime import …`。

**为什么这不是洁癖**：`create_agent_level_instances` 有四个调用点，只有
`backend/routes/auth.py` 后面紧跟了一次同步。`migration/applier.py`（bundle /
迁移导入）和 `backend/integrations/arena/arena_provisioning_service.py` **都
没有** —— 导入进来的 agent 若不装 skill，名录里就一直是空 capabilities，要等它
第一次跑起来才被每轮 hook 修好，正是本次要终结的「发现要等第一次 turn」。改走
seam 后这两条路径**不用各自改动**就修好了。

两条测试钉住：provisioning 之后名录行存在且 capabilities 非空；本文件源码里
不再出现对该表的写调用（`'"bus_agent_registry"'`）。

# instance_factory.py — Instance 的创建与装载工厂

## 为什么存在

Agent 的能力不是代码里写死的，而是一批 **Instance 行**（`module_instances`）。
一个 agent 刚被创建时手上什么都没有，这个文件负责把「一个 agent 应该默认拥有
哪些 Instance」这件事集中在一处：Awareness / SocialNetwork / BasicInfo /
MessageBus / Lark / HomeAssistant 各一条 agent 级实例（`is_public=1`，跨 Narrative
共享），外加按需创建的 chat / job 实例。

没有它，每个建 agent 的入口都要自己拼一遍 Instance 记录 —— 而入口有四个（HTTP
创建、bundle/迁移导入、arena provisioning、`ensure_agent_instances_exist` 补齐），
四份必然漂移。

## 上下游

- **被谁调**：`backend/routes/auth.py`（创建 agent）、`migration/applier.py`
  （导入）、`arena_provisioning_service.py`、以及自己的
  `ensure_agent_instances_exist`（幂等补齐，老 agent 缺实例时走这条）
- **依赖谁**：`InstanceRepository` / `InstanceNarrativeLinkRepository` 写表；
  [[agent_discovery_sync]] 写同伴发现行；`module_registry` 把 `module_class` 字符串
  映射到类

## 设计决策

**幂等是硬要求**，不是优化：四个入口都可能对同一个 agent 调用（尤其
`ensure_agent_instances_exist` 就是为了补齐历史 agent），所以每个
`_create_*_instance` 都先查后建，已存在就返回现有记录。

**每个模块一个私有方法**而不是一张配置表：各模块的初始 `config` / `state` /
`keywords` 差别大（Lark 要 binding 占位、HomeAssistant 要 setup 标记），配置表
会变成一堆特例分支。

## Gotcha

- 创建实例**不代表**该能力可用：Lark 实例存在只说明「这个 agent 可以绑 Lark」，
  真正的绑定在 `instance_lark_bindings`。
- 这里的失败一律 best-effort（log + 继续）：建 agent 这个动作不能因为某个附属
  实例没建成而整体失败，调用方随后可用 `ensure_agent_instances_exist` 补齐。

## 2026-09-04 · channel instances from descriptors (batch 4e)

`_create_lark_instance` / `_create_home_assistant_instance` are replaced by `_create_channel_instance(agent_id, descriptor)` over `_channel_instance_descriptors()` — every registered channel whose descriptor has a `module_ref` and `meta["agent_instance"]` (description / keywords / topic_hint). Instance-id prefix = the channel name without underscores (`lark_…`, `homeassistant_…`, unchanged), module class = the `module_ref` class name. `ensure_agent_instances_exist` builds its creators the same way, so an agent created before a channel plugin was installed gets the instance on next load — the factory holds no channel list.

## 2026-09-04 · declared instances and role lookups (batch 5b.2)

Agent-level instances come from `ModuleConfig.agent_instance` (`_agent_instance_modules` / `_create_declared_instance`, priority order) — core four, Lark, Home Assistant and any plugin module alike; `ensure_role_instance(agent_id, role)` is what provisioning, migration and the job→entity sync use instead of naming a module; `create_chat_instance` / `create_job_instance` resolve their class through roles "chat" / "jobs" and fail loud when that plugin is disabled.
