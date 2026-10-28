---
code_file: packages/narranexus-contracts/src/narranexus/contracts/table.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2a）— `backend.tables` 位的契约：纯数据的表定义

刻意**不**复用内核的 `TableDef`（契约包不能 import `xyz_agent_context`）：`TableSpec/ColumnSpec/IndexSpec` 是
frozen dataclass，内核 `schema_registry.register_table` 负责转换。双方言列类型都必填（与内核规则一致）。
`check_owner`：非 `builtin.` owner 的表名必须带 `ext_<owner>_` 前缀（点/横线转下划线），让插件表可辨认、
不撞核心表、卸载时能列清单（默认保留数据）。`MigrationSpec` 是插件自管数据迁移的形状（version + 符号），
执行器在内核 db 迁入时接。

## 2026-09-07 — ext_<owner>__ prefix is injective

The isolation prefix is terminated by a DOUBLE underscore: with a single one, acme.weather's prefix was a prefix of acme.weather_x's tables and PluginDb let one plugin read and write its sibling's data. Plugin ids never contain '__' or end in '_' (manifest PLUGIN_ID_RE), so two prefixes are prefix-related only when the ids are equal.

## 2026-09-07 — 前缀文档改为事实（round-2 K2-I3）

table_prefix_for 用 plugin_id_slug；docstring 不再声称对 id 单射，而是说明同 slug 的两个 id 由安装器/boot 拒绝。
