---
code_file: plugins/builtin.awareness/src/narranexus_plugins/awareness_module/awareness_module.py
last_verified: 2026-09-04
---

## 2026-08-19 — 下面 2026-08-10 那条的指路已经过期

那条说改名事务和两个 DB 助手「移到 [[_awareness_writes]]」——本次改动把它们**再次
搬走**了，历史正文保留（哪一轮知道了什么要可追溯），但按它去找会扑空：

| 找什么 | 现在在哪 |
|---|---|
| `apply_agent_profile_change`（改名事务） | `agent_profile/_agent_profile_impl/profile_write.py`（[[profile_write]]） |
| `_same_owner_name_holder` | 同上 |
| `_record_identity_change` | 仍在 [[_awareness_writes]]，但**改名为公开的 `record_identity_change`**（跨包调用，不再是私有符号） |

搬走的理由见 [[_overview]]：事务写 `agents` 行、刷 `bus_agent_registry`、还接
`is_public`/`created_by`，没有一样是 Awareness 的职责——Awareness 只是它其中一步。
留在本模块的是真正属于它的那部分：身份记录段的常量与助手、两个写入器、
`retire_self_name`、以及 MCP 渲染器。

## 2026-08-10 — update_agent_profile routes through AgentDataStore

`update_agent_profile` MCP 工具不再直连 db：改为
`get_agent_data_store().update_agent_profile(agent_id, new_name, new_description)`
（[[store]]）。改名事务整套逻辑 + 身份笔记 build/merge + 两个 DB 助手
（_same_owner_name_holder / _record_identity_change）**移到** [[_awareness_writes]]
`update_agent_profile_from_args`，DirectStore（本地不变）与 backend 孪生路由 [[profile]]
（云，无 db 凭据）都调它 → byte-parity。返回 str（动态状态串，见 _awareness_writes）。
MATCHED/CHANGED 等值短路随逻辑一起搬走、语义不变。下方历史条目记录的仍是该事务的设计初衷。


## 2026-08-10 — update_awareness routes through AgentDataStore

`update_awareness` MCP tool no longer touches the db directly; it calls
`get_agent_data_store().update_awareness(agent_id, new_awareness)` (module/
data_access). DirectStore keeps the exact prior behaviour; HttpStore (when
NARRANEXUS_BACKEND_URL is set) routes it through the backend API so mcp needs
no db creds. Blueprint P0 — behaviour-preserving.


## 2026-08-05 — review 修两处：记忆截断丢内容、description 在 MySQL 上假报错

1. **`merge_identity_change_note` 会吃掉 section 之后的内容**（这正是它
   docstring 里承诺绝不发生的事）。原实现 `partition` 之后把 marker 之后的
   **全部文本**当 section，只留 `- ` 开头的行 → 一旦 identity section 落在
   profile 中间（`update_awareness` 让模型整篇重写、提示词还要求保持完整结构，
   所以这是常态），下一次改名会静默删掉它下面的所有小节。改成**按下一个 `##`
   标题切断**，尾部原样接回。原有三个用例的 section 都恰好在末尾，所以覆盖不到
   —— 补了「section 后面还有两个小节 + 一行自由文本」的用例。
2. **description 没做等值短路**。`update_agent` 返回 `cursor.rowcount`：MySQL
   （dev/prod，建池未设 `CLIENT_FOUND_ROWS`）算 **changed** rows，SQLite 算
   **matched** rows。于是「把描述写成和现值相同」在云上返回 0 → 给模型
   `Error: the update did not apply`，本地却是成功。而 §5 提示词明确鼓励
   「whenever the answer changes」反复调用。照 name 分支加等值判断即可。
   注意：断言「没报错」在 SQLite 上恒绿，所以真正钉住修复的是断言**走了
   "No changes needed" 分支**那一条。

## 2026-08-04 — `update_agent_name` → `update_agent_profile`：改名是一次事务

P1 段02 ① 的修复（prod evt_1f9c6680）。用户把第一个 agent 命名为「凑企鹅」，
08:42/43 用改名工具把**同一个名字转给第二个 agent**。旧工具只写
`agents.agent_name`，而 agent 的身份感知住在**自由文本的长期记忆里**，于是第一个
agent 仍自述「凑企鹅 is actually my own agent name」——DB 说的是另一回事，而那个
名字此时已经合法地属于别人。一列改不动几千字叙事。

新工具做四件事（缺一件就还是原来的 bug）：

1. **两个字段都能写**：`new_name` / `new_description`，都可单独给。描述的
   docstring 明确写清读者是**别的 agent**（见 [[prompts]] §5）。
2. **改名同时归档一条身份更正**：`build_identity_change_note` +
   `merge_identity_change_note` 往 Awareness profile 里**追加**
   `## Identity Changes (platform record)` 一行（带日期、点名两个名字、明确
   **retire 旧名**并说明它可能已属于别的 agent）。profile 每轮原文注入，所以下一轮
   即生效。**是追加不是改写**——agent 对 owner 的观察不是我们该编辑的东西，为改名
   丢掉它会是比原 bug 更糟的 bug。section 上限
   `MAX_IDENTITY_CHANGE_ENTRIES=5`，新的留下，避免无界增长吃掉它所在的上下文窗口。
3. **同 owner 重名如实告知、不拦**（Owner 2026-08-04 定）：倒手是用户故意的，
   **静默**才是事故温床。返回值里点出当前持有者的 agent_id，让 agent 去问 creator。
   跨用户同名不算冲突，绝不跨账号报。
4. **写完立刻刷同伴名录**（[[agent_discovery_sync]]），不等下一轮。

`_record_identity_change` 是 best-effort：名字已经写进去了，之后再抛异常会告诉
模型"改名没成功"——那是假话；退化成旧行为严格优于报假失败。

无兼容壳（铁律 #2）：`update_agent_name` **删除**，有测试钉住它不许回来——只写名字
的工具本身就是绕过身份更正的那个 bug。

# awareness_module.py — AwarenessModule 实现

## 为什么存在

AwarenessModule 是让 Agent 拥有"长期记忆用户偏好"能力的组件。它在每轮对话的数据收集阶段把 Awareness Profile 加载到 `ctx_data.awareness`，这个字段被 `prompts.py` 的 `{awareness}` 占位符填入系统提示，让 Agent 在整个对话中都知道"这个用户喜欢什么风格、有什么约定"。

**Hook 实现**：实现了 `gather`（从 `instance_awareness` 表加载 profile），未实现 `after_turn`（用户偏好更新通过 MCP 工具而非 hook 完成）。

**MCP 端口**：7801

**Instance 模型**：Agent 级别（`is_public=1`），每个 Agent 只有一个实例，通过 `InstanceFactory.ensure_agent_instances_exist()` 在 Agent 创建时自动初始化。

## 上下游关系

- **被谁用**：`ModuleLoader` 自动加载（capability module）；`HookManager` 调用 `gather`；`ModuleRunner` 启动 MCP 服务器
- **依赖谁**：`InstanceAwarenessRepository`（读写 profile 文本）；`InstanceRepository`（通过 agent_id 查找 instance_id）；`AgentRepository`（更新 agent_name）

## 设计决策

**`_get_instance_id()` 的双路径查找**：优先用 `self.instance_id`（由 `ModuleLoader` 注入），如果为 `None` 就通过 `agent_id + "AwarenessModule"` 查询数据库。这个 fallback 保证了 bootstrap 或数据库不完整时模块仍能工作，代价是一次额外的数据库查询。

**MCP 工具里用 `AwarenessModule.get_mcp_db_client()`**：MCP 工具在独立进程/线程里运行，不能使用 `self.db`。`get_mcp_db_client()` 是类方法，在当前进程里懒创建专属连接。

**首次使用自动创建默认 profile**：如果 `instance_awareness` 表里没有记录，`gather` 会自动写入一个默认的 "helpful assistant" profile，而不是让 `ctx_data.awareness` 为空。这防止了 LLM 因空 awareness 报错或行为异常。

## Gotcha / 边界情况

- **`instance_id` 为 `None` 时的行为**：如果 `_get_instance_id()` 返回 `None`（数据库里找不到实例记录），模块会用硬编码的默认 awareness 字符串继续运行，并打 warning 日志。这种情况通常说明 Agent 的实例记录没有正确初始化。
- **`init_database_tables()` 里的 SQL 是 MySQL 语法**：`DATETIME(6)` 和 `ON UPDATE CURRENT_TIMESTAMP(6)` 是 MySQL 专有语法，SQLite 不支持。实际表创建通过 `utils/database_table_management/create_instance_awareness_table.py` 进行，这个方法在生产中很少被直接调用。

## 新人易踩的坑

- 以为修改 Awareness Profile 可以通过直接写 `ctx_data.awareness` 来持久化——实际上 `ctx_data.awareness` 是每轮重新从数据库加载的，持久化必须通过 MCP 工具 `update_awareness` 调用 `InstanceAwarenessRepository.upsert()`。
- 在 `gather` 里调试时看到 awareness 是旧值——因为 MCP 工具在独立进程里更新了数据库，但当前进程的连接缓存可能还持有旧连接状态，通常重启即可。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.

## 2026-09-04 · declares its platform metadata (batch 5b)

`get_config()` is static and carries what the platform used to table about this module (default / base / always-load membership, instance prefix, role, display, decision metadata as applicable).

## 2026-09-04 · declares its agent-level instance (batch 5b.2)

`agent_instance` in `get_config()` (description / keywords / topic hint) replaces the factory's or descriptor's copy; the InstanceFactory creates it for every agent.

## 2026-09-04 · declares its discovery / long-run flag (batch 5d)

`discovery_hidden` (awareness / basic info / message bus) or `long_running_instances` (skills) moved from platform lists into `get_config()`.
