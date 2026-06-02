---
code_file: src/xyz_agent_context/repository/event_memory_repository.py
last_verified: 2026-05-29
stub: false
---

# event_memory_repository.py — Narrative 级 Memory 持久化（EventMemoryRepository）

## 为什么存在

为 Module 提供 narrative 级的结构化 Memory 存储（每个 module 一张
`json_format_event_memory_{module}` 表 + 共享的 `module_report_memory` 表），
全部按 narrative_id 隔离。这是 Module Instance「记忆」的物理载体——区分同一个
module 在不同 narrative 下的状态。

## 为什么在 repository/ 而不是 module/

2026-05-29 之前它叫 `EventMemoryModule`，住在 `module/event_memory_module/`，
继承 `XYZBaseModule`。这让 ChatModule `import` 了一个兄弟「module」，表面违反
铁律 #3。但它**从来不是真 module**：不在 MODULE_MAP、没有 MCP server、它的
Module hooks 从不被调度。本质就是数据访问层，所以移到 repository/、去掉 Module
基类、改名 EventMemoryRepository、用普通构造函数注入 db client。
ChatModule 现在从 repository 层依赖它（modules → repository 是允许方向）。

## 坑

- 表是**惰性创建**的（`ensure_*_table` + `_checked_tables` 缓存），不走
  schema_registry / auto_migrate——因为表名按 module_name 动态生成
  （`json_format_event_memory_{module}`），不是静态 schema。
- 唯一真实消费者是 ChatModule（实例化 + 调 search/add_instance / update_report）。
  ChatModule 上的属性名仍叫 `event_memory_module`（保留以免动一堆测试），但指向的
  是本 Repository 实例。
- SQL 用 MySQL 方言（`information_schema`、`ON DUPLICATE KEY`）；本地 SQLite 下
  这些表路径可能不可用——属于历史遗留，narrative 级 module 记忆在纯 SQLite 本地
  模式下未必启用。
