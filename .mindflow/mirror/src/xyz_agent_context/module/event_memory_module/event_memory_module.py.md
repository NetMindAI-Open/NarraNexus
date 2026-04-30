---
code_file: src/xyz_agent_context/module/event_memory_module/event_memory_module.py
last_verified: 2026-04-28
---

## 2026-04-28 status — `update_report_memory` / `get_report_memory` 现处于"无人读"待醒状态

设计意图保留：每个 Module 在 `hook_after_event_execution` 调
`update_report_memory(narrative_id, module_name, report_memory)` 把
自己的最新状态报给 Narrative；Narrative 编排层调
`get_report_memory(narrative_id, module_name?)` 读这些报告决定要不要
激活某个 Module。

实际现状（2026-04-28 全仓盘点）：
- **`get_report_memory` 无任何调用方** —— Narrative 编排层从来没接通这条
  消费路径
- **`update_report_memory` 唯一调用方** 是 `chat_module.py:725-746`，
  那一段 2026-04-28 已注释（见 `chat_module.py.md` 的当日 entry）
- **底层表 `module_report_memory` schema 漂移**：实际 SQLite/MySQL 表
  里还留着一个早期设计的 `instance_id NOT NULL` 列。当时表按
  `instance_id` 索引（每个 module instance 一条 report）。后来重设计成
  `(narrative_id, module_name)` 唯一索引，`schema_registry.py` 注册的是
  新版列；`auto_migrate()` 不删旧列（铁律 #6 数据库不做危险变更），所以
  `instance_id NOT NULL` 还在。当前 INSERT 不填它，写入必然报
  `NOT NULL constraint failed: module_report_memory.instance_id`。

恢复这条特性时要做的：
1. 解决 schema 漂移 —— 写 one-shot migration（仿
   `utils/one_shot_migrations.py` 套路）：SQLite 走"create new + copy +
   drop + rename"；MySQL `ALTER TABLE DROP COLUMN`。注意
   `module_report_memory` 是缓存性的（每次 hook 重写），数据丢了不心疼；
   要紧的是清掉旧列。
2. 把 `chat_module.py:725` 的那段 commented block 翻回来。
3. 在 Narrative 编排层接通 `get_report_memory` 的消费方。

未做以上三步前，方法本身留着是为了**接口契约不破裂** —— 其他 Module
（如果哪天 SocialNetworkModule / JobModule 也想报状态）写代码时还能
import 到 `update_report_memory`。但调一次就报错 + log 噪音，所以现在
全部不调用维持。

# event_memory_module.py — Narrative 级别存储服务

## 为什么存在

`EventMemoryModule` 为其他 Module 提供统一的 Narrative 级别持久化接口。没有它，`ChatModule` 需要自己管理表名约定、直接写 SQL；有了它，各 Module 只需调用 `add/search_instance_json_format_memory` 就能获得隔离的 JSON 存储。

**没有实现的功能**：`hook_data_gathering`、`hook_after_event_execution`、MCP 服务器——这个 Module 只作为服务被其他 Module 调用，不参与常规的 hook 流程。

## 上下游关系

- **被谁用**：`ChatModule`（主要消费方）在 `__init__` 里创建 `EventMemoryModule` 实例并在 hook 中调用；`SocialNetworkModule` 同样在 hook 中调用
- **依赖谁**：`DatabaseClient`（直接写 SQL，动态表名）；表名约定 `instance_json_format_memory_{module_name}`

## 设计决策

**动态表名约定**：每个使用 EventMemoryModule 的 Module 有自己独立的表（`instance_json_format_memory_chat`、`instance_json_format_memory_social_network` 等）。这避免了不同 Module 的数据混在一张大表里，便于单独查询和删除。代价是表数量随模块数增长，且表名是字符串拼接（`module_name` 参数），不是编译期常量。

**`instance_id` 是主键维度**：与旧版本用 `narrative_id` 不同，重构后改为用 `instance_id`（Module 实例 ID）作为数据隔离维度，因为同一个 Narrative 里一个 Module 可能有多个实例（如 JobModule）。

**不继承 XYZBaseModule**：等等——实际上它**确实继承了** `XYZBaseModule`（`class EventMemoryModule(XYZBaseModule)`），但没有注册在 `MODULE_MAP` 里，所以永远不会被 `ModuleLoader` 自动加载。它只通过直接实例化（`ChatModule.__init__` 里 `self.event_memory_module = EventMemoryModule(...)`）来使用。

## Gotcha / 边界情况

- **`search_instance_json_format_memory` 找不到记录时返回 `None`**（不是空 dict）。调用方必须处理 `None` 的情况，`ChatModule` 里用 `existing_memory.get("messages", []) if existing_memory else []` 做了防护，新的调用方也需要这样处理。
- **`add_instance_json_format_memory` 是 upsert 语义**：如果对应 `instance_id` 已有记录，会覆盖整个 JSON blob，不是增量追加。追加逻辑需要调用方先 `search` 再合并再 `add`（`ChatModule` 就是这样做的）。

## 新人易踩的坑

- 试图把 `EventMemoryModule` 加入 `MODULE_MAP` 使其被自动加载——它不应该被自动加载，它是工具类而非 capability/task module。它没有 `get_config()` 和 `get_mcp_config()` 的有意义实现（会报错）。
