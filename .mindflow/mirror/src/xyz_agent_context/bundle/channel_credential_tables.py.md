---
code_file: src/xyz_agent_context/bundle/channel_credential_tables.py
last_verified: 2026-07-13
stub: false
---

## 2026-07-13 — Lark identity key fixed to app_id

Lark's `identity_cols` was `["profile_name"]`, which is wrong: `profile_name` is
`build_profile_name(agent_name, agent_id)` — agent-derived and preserved verbatim on
import, so it never matches in the target env → the clash check was a silent no-op.
Changed to `["app_id"]` (the Lark app = the real bot identity that owns the single WS
slot, matching the other channels' `team_id`/`bot_user_id`). Test:
`tests/bundle/test_channel_credentials.py::test_lark_clash_keys_on_app_id_not_profile_name`.

# channel_credential_tables.py — single source of truth for IM credential bundling

## 为什么存在

"带凭据打包"功能里，三个调用点需要**同一份**每张 IM 凭据表的元数据，且不能各写各的、发生漂移：

- `builder.py` —— 勾选导出时读哪几张表。
- `importer.py` —— 导入时把哪一列强制置 0（防双连不变式），以及**哪些表要豁免**通用的 user 归属重写（它们的 owner 列是 IM 侧 id，不是 NarraNexus user_id）。
- `preflight` —— 哪几列构成 bot 身份唯一键，用来检测"该 bot 在目标环境已绑定"。

把这份表-元数据集中在一个常量 `CHANNEL_CREDENTIAL_TABLES` 里，避免 builder/importer/preflight 三处对"启用位叫什么、身份键是哪几列"各执一词。

## 上下游关系

- **被谁用**：`bundle/builder.py`（导出选表）、`bundle/importer.py`（force-inactive + 归属豁免 + 冲突检测）、`bundle/id_field_map.py`（登记同一批表的 agent_id，注释相互指向）
- **依赖谁**：无（纯常量 + TypedDict）

## 设计决策

### 每张表两个字段

- `active_col`：导入时强制置 0 的列。**lark 是 `is_active`，其余四个是 `enabled`** —— 列名不统一是历史，别想当然。
- `identity_cols`：bot 身份唯一索引对应的列。空列表 = 该表没有 bot 身份唯一约束（wechat / narramessenger），导入时 agent_id 永远新铸，不会冲突。

### 激活语义（为什么 force-inactive）

IM 凭据一律以停用态导入。用户必须在新环境手动激活，这一步才是"抢占该 app 唯一那条 WS/连接槽位"的动作，防止迁移后的 Agent 从源、目标两个环境同时连同一个 bot。

## Gotcha

- 加新 IM 频道时，若要纳入打包，必须同时在这里登记 + 在 `id_field_map.STRUCTURED_ID_FIELDS` 登记 `agent_id` + 确认凭据表有 `active_col`。三处漏一处，凭据要么带不走、要么导入后归属错乱、要么无法激活。
