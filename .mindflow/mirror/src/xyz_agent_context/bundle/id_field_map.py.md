---
code_file: src/xyz_agent_context/bundle/id_field_map.py
last_verified: 2026-07-10
stub: false
---

## 2026-07-10 — IM channel credential tables registered

Added `{"agent_id": "agent"}` for the six credential tables (lark_credentials +
channel_{slack,telegram,wechat,discord,narramessenger}_credentials) so the opt-in
credential import remaps each credential's `agent_id` old → new (otherwise the
imported key dangles at the source agent_id). `agent_id` is the ONLY internal id
on these tables; everything else (app_id, tokens, owner_user_id, bot_user_id) is
IM-namespace and left untouched. Kept in sync with
`bundle/channel_credential_tables.py`.

## 2026-06-08 — social_entities key rename

The id-field-map key `instance_social_entities` was renamed to `social_entities`, matching the bundle section name now that entities are sourced from `memory_entity` via the repo rather than the retired table.

# id_field_map.py — ID Rewrite Layer 2 (PRD §8.11)

## 为什么存在

Layer 1 (`id_schema.py`) 知道**哪些字符串模式是 ID**，但不知道**哪个表的哪一列是 ID**。Layer 2 这份字典告诉 importer：处理 events 表时 `event_id / agent_id / narrative_id` 都要按 map 替换；处理 instance_social_entities 时 `instance_id` 是 ID 列等等。

## 上下游关系

- **被谁用**：`bundle/importer.py` 的 `rewrite_row(table, row)` 函数
- **依赖谁**：`id_schema.ID_KINDS`（间接）

## 设计决策

### 维护责任

每加一张新表 / 新 ID 列**必须**在 `STRUCTURED_ID_FIELDS` 登记。Layer 3 (CI 反向检查脚本 `check_id_field_coverage.py`) 应该跑一遍 `schema_registry` 找所有 `*_id` 列，对照本字典报缺。

> ⚠️ **Layer 3 v1 没做** — 维护靠人盯。漏一列在运行时静默漏 rewrite。

### `gen_new_id(kind)` 用 `secrets.token_hex(6)`

12-hex 字符 = 48 bit 随机，碰撞极低。前缀依 kind 不同（agent / evt / nar / inst / msg / job / team / ch / mcp / art）。

### 1.1 新增（2026-05-15）

- `STRUCTURED_ID_FIELDS["instance_artifacts"] = {"artifact_id": "artifact", "agent_id": "agent"}` —— artifacts 入包必备
- `STRUCTURED_ID_FIELDS["mcp_urls"] = {"mcp_id": "mcp", "agent_id": "agent"}` —— importer 直接 write-through 进 mcp_urls 需要这条登记，让 `rewrite_row` 能把 bundle 里的 mcp_id/agent_id 正确换成本机的新 ID

## Gotcha

- JSON path 形式 `"col[*].sub_id": "instance"` 我**写了但 importer 还没真支持**这种 jsonpath。当前实现只处理顶层列名 + 自由文本兜底。这是已知的 Layer 2 半残点。
