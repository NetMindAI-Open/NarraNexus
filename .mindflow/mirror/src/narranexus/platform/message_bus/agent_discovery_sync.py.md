---
code_file: src/narranexus/platform/message_bus/agent_discovery_sync.py
last_verified: 2026-09-04
stub: false
---

## 2026-08-19 (更正) — 事务不在 [[_awareness_writes]] 了

本文件下面那条 2026-08-18 的条目说「统一经由 `_awareness_writes` 的
`apply_agent_profile_change`」——**同一个 PR 里它已经搬到**
`narranexus/platform/agent_profile/`（见 [[_overview]]）。改名侧调用本函数的仍然只有
那一个事务，只是它换了住址。

## 2026-08-18 (二改) — manyfold 的 POST 也进来了

同日第一条只补了 manyfold 的 `PATCH`。`POST /manyfold/agents` 在 agent 已存在时
同样改名，同样从没刷过名录；它现在也经由 [[_awareness_writes]] 的
`apply_agent_profile_change` 调本函数。改名侧的调用方仍然只有那一个事务。

## 2026-08-18 — 调用方清单补两处，且改名侧收敛到一个入口

「所有变更点都调它」这句的清单原来漏了一个、且有一个是假的：

- **manyfold 的 `PATCH /manyfold/agents/{id}` 从来没调过**（改名后同伴目录停在旧名
  直到该 agent 跑一轮）。现已补上——但不是直接调本函数，见下。
- 名字/描述编辑这一支现在**统一经由** [[_awareness_writes]] 的
  `apply_agent_profile_change`：用户侧路由、manyfold 路由、agent 自己的
  `update_agent_profile` 三条路都走那一个事务，由它调本函数。所以本函数的改名侧
  调用方**只剩一个**，不再是三处各自记得调。

创建那一侧的清单**当时是错的**：二改写着「创建（[[auth]] / [[provision]]）……不变」，
但 manyfold 的建号分支既不走 provision 也不自己调，第二轮审查抓出来了。已补上
（见 [[agents]] 三改）。现在创建侧是：[[auth]] / [[provision]] / migration applier /
arena provisioning / **manyfold 建号**；技能安装、技能对账、每轮 hook 不变。

# agent_discovery_sync.py — agent 对同伴那一面的唯一真相点

## 2026-08-05 — 从 services/ 搬到 message_bus/，并成为真正的唯一写入者（review）

两处收口：

1. **位置**：`services/` 按 CLAUDE.md 架构表是**后台服务层**（ModulePoller /
   InstanceSyncService 这类独立进程）。这个文件不是 worker——它是被 HTTP 路由、
   MCP 工具、安装管线、每轮 hook **同步调用**的策略函数，而且管的是
   `bus_agent_registry`（message_bus 自己的表）。搬进 [[message_bus]] 包后，
   backend 路由可以直接 import（message_bus 是包不是 module，不触发铁律 #3），
   policy 与 [[local_bus]] 的读写面落在同一个域里。
2. **「唯一」变成事实**：`bus_register_agent` MCP 工具原来是同一行的第二个
   写入者，且写 `owner_user_id=""`，而 `search_agents` 按该列过滤——调一次就
   从同 owner 搜索结果里消失。该工具已删，见
   [[_message_bus_mcp_tools]] 同日条目。

## 为什么存在

P1 段02（prod 实锤 2026-08-03）：用户两个 agent 界面都显示"配置完成"，A2A 询问
却被回「还没配置完成」，收件箱为空。不是 prompt 问题，是数据层：

1. `agents.agent_description` 只在**创建时**写过一次占位符
   （`"A new agent ready for configuration"`），之后任何配置 / skill / 改名都
   不更新；
2. `bus_agent_registry` 只有**一个**写入者——`MessageBusModule.gather`
   里的一段内联代码，它把那个占位符快照进去，并把 `capabilities` **硬编码成
   `[]`**。prod 全表 488 条都是这样；
3. 于是 `bus_search_agents`（`capabilities LIKE ? OR description LIKE ?`）对
   任何查询都返回空，`bus_get_agent_profile` 把配置好的 agent 报成"待配置"，
   **询问方 LLM 据此判断对方没准备好、拒绝发消息**（evt_feb1f6ae）→ 收件箱空。
   A2A 发现机制是**系统性死亡**，不是偶发。

## 两条设计规则（本模块存在的意义）

**① 重算，不信任存下来的摘要。** `capabilities` 从平台已经拥有的事实推导：
已安装技能（`skill_installations` status=installed）+ 活跃模块类
（`module_instances`，剔掉 BasicInfo/Awareness/MessageBus——人人都有，搜索面
纯噪音）。这样发现能力不取决于 agent 记不记得描述自己（铁律 #15），也不含任何
场景硬编码（铁律 #4）。agent 自己写的 description 原样使用；**legacy 占位符
按"什么都没说"处理，绝不再发布出去**。

**② 一个 seam，所有变更点都调它。** 创建（[[auth]]）、名字/描述编辑
（同上 + [[awareness_module]] 的 `update_agent_profile`）、技能安装
（[[install_pipeline]]）、技能对账（[[skill_sync_service]]）、以及每轮的
bus 钩子（[[message_bus_module]]，现在只是**幂等兜底**）。注册因此发生在
**创建那一刻**——而不是第一轮，那正是"配置好但没跑过"的 agent 对同伴完全不
存在的原因（票上目标 2）。

## 刻意不做的事

**不用 LLM 从 Awareness 生成描述。** 那会把一次付费、不确定的调用挂在创建路径
和每次技能安装上。切法是：description = **稳定的自我定位**（bootstrap 写、改名
或重新配置时更新），capabilities = **每次变更机械重算**。经 Owner 确认
（2026-08-04）。

## Gotcha

- `sync_agent_discovery` **永不抛异常**，返回 bool。所有调用方的正职都是别的事
  （建 agent、装技能、给这一轮攒 context），不能因为发现元数据没刷新而失败。
- `capabilities` 排序后写入：这行每轮都会重写，稳定值让写入真的成为 no-op。
- agent 不存在（已删）→ 返回 False 且不建行，不是异常。

## 上下游

- 表访问：[[agent_registry_repository]]（repo 层，不在模块里——铁律 #3）
- "描述算不算没设置"的判断：[[entity_schema]] 的 `is_agent_description_unset`
- 读这张表的：`LocalMessageBus.search_agents` / `get_agent_profile`
- 测试：`tests/message_bus/test_agent_discovery_sync.py`

## 2026-09-04 · no module list (batch 5d)

`_uninteresting(module_class)` reads `ModuleConfig.discovery_hidden` instead of the frozen three-name set.
