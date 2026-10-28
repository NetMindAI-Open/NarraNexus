---
code_file: plugins/builtin.awareness/src/narranexus_plugins/awareness_module/routes.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 宿主依赖改走 `narranexus.sdk.web`（批 6c，G2-I1）

批 6b 把 router 搬进插件包时，`from backend.*` 没跟着走：本文件当时还在 import 宿主的
私有模块（`routes._ownership` / `routes._mcp_egress` / `routes.dashboard.routes` 的下划线
函数）或非契约的公开符号（`backend.auth` / `backend.auth_errors` / `backend.config`）。
现在全部改成 `narranexus.sdk.web` 的 seam（实现见 [[plugin_sdk_host]]，形状见
[[web]]）。这不是改 import 路径的洁癖——第三方照抄这个 router 时，
`assert_owned` / `resolve_current_user_id` 一个都拿不到，只剩「自己重写鉴权」
（2026-08-12 那批 IDOR 的来源）这一条路。`pyproject.toml` 的
`plugins never import the host (backend)` 契约把这条线钉死，豁免列表为空。

## 2026-08-19 — 云端路由同样保住平台身份记录

`PUT /agents/{id}/awareness` 原来直接 upsert 模型给的整段文本，于是模型一次重写
就把 `## Identity Changes (platform record)` 删掉了。与 [[store]] 的本地路径同一处
理：先读旧值，`carry_over_platform_record` 接回那一段。

这一半**更要紧**：深圳那次事故就发生在 prod，而 prod 的 `update_awareness` 走的正是
HttpStore → 本路由。第一版只修了本地，等于保护了没出事的那个环境。

## 2026-08-11 — owner-only + GET 不再自动建实例（安全审计 IDOR/P0-1）

两个端点原本**无任何 ownership 校验**、只凭 URL 里的 `agent_id` 操作：任何登录
用户可读/改任意 agent 的自我认知（PUT 尤甚——覆写人设影响后续所有对话）。
修复:GET/PUT 均先 `await assert_owned(request, agent_id)`（`backend/routes/_ownership.py`），
**放在 try 之前**——否则 assert_owned 抛的 403/404 会被 except 吞成 200。
cloud 模式强制 owner；local 模式 no-op（无 per-request 身份，单可信用户）。

同时**GET 不再自动建实例**:改用 `_find_awareness_instance`，无实例直接返回
not-found，不再 `_ensure_awareness_instance`。读变回无副作用,也堵掉"对任意
id 探测即凭空造实例"。PUT 仍保留 `create_missing` 默认自动建（前端契约不变）。

## 2026-08-10 — PUT 增加 `create_missing` 开关（MCP 数据访问 seam 的 parity 半边）

自动建实例是**前端**契约(默认不变);MCP seam 的 HttpStore 传
`create_missing=false`:agent_id 是 LLM 自由填写的工具参数,直连路径下未知
id 是报错,Http 化后不能反而变成"给任意 id 凭空造实例"。关不建时按本路由
的失败形状返回 200+success:false,error 文案与 DirectStore 的
no-instance 消息逐字对齐(parity 测试钉住)。`_ensure_awareness_instance`
拆出 `_find_awareness_instance` 供只查不建。

# agents/awareness.py — Agent Awareness 读写路由

## 为什么存在

Awareness 是 Agent 的自我认知配置——它知道自己是谁、有什么能力、适用于哪些场景。这些信息存储在 `instance_awareness` 表里，通过 `AwarenessModule` 的实例 ID 关联到 Agent。这个路由文件暴露 GET/PUT 两个接口，让前端能读取和编辑 Awareness 内容。

## 上下游关系

- **被谁用**：`backend/routes/agents/core.py` 聚合并挂载到 `/api/agents`；前端 `AwarenessPanel` 组件
- **依赖谁**：
  - `InstanceRepository` — 查询或创建 `AwarenessModule` 实例
  - `InstanceAwarenessRepository` — upsert awareness 内容到 `instance_awareness` 表
  - `narranexus.platform.utils.db.db_factory.get_db_client` — 直接查询 `instance_awareness` 表读取结果

## 设计决策

**自动创建实例的 `_ensure_awareness_instance`（仅 PUT）**

如果 Agent 还没有 `AwarenessModule` 实例，**PUT** 默认会自动创建一个（`create_missing=true`，前端契约），而不是返回错误；理由是 Awareness 对每个 Agent 必要，自动补齐比强迫调用者先建实例更好用。**GET 不再自动建实例**（2026-08-11 改，见顶部）：读用 `_find_awareness_instance`，无实例返回 not-found——GET 恢复无副作用，避免对任意 id 探测即造实例。

**分开 Repository 和直接 DB 查询**

写操作用 `InstanceAwarenessRepository.upsert()`，读操作用 `db_client.get_one()` 直接查表，没有通过 Repository 封装。这是轻微的不一致，但读 Repository 的实现本质上也是 `get_one`，直接调没有额外风险。

## Gotcha / 边界情况

- **Awareness 数据不存在时 GET 返回 `success=False`**：即使实例创建成功，如果 `instance_awareness` 表里还没有这个实例的记录（比如 Awareness 从未被写过），GET 会返回 `success=False, error="Awareness data not found"`，而不是空数据。前端需要处理这个情况，把它区别于真正的错误。
- **PUT 之后立即重读**：upsert 成功后会再次 `get_one` 读取刚写入的数据并返回，这是为了确保返回值反映数据库的实际状态（比如 `updated_at` 字段由数据库生成）。

## 新人易踩的坑

`instance_awareness` 表的主键是 `instance_id`，而不是 `agent_id`。必须先拿到实例 ID（GET 用 `_find_awareness_instance` 只查不建、PUT 用 `_ensure_awareness_instance` 查不到则建），再用实例 ID 查询，不能用 agent_id 直接查 `instance_awareness`。

## 2026-09-04 · data-access providers (batch 3c.4)

`ROUTES` — this router is the `backend.routes` contribution of builtin.awareness (prefix `/api/agents`, mounted by `backend/plugins_host`), no longer included by `routes/agents/core.py`; disabling the plugin makes these paths 404 together with the MCP-side provider.

## 2026-09-07 — moved into the plugin package

This router is the plugin's own contribution (`narranexus_plugins.awareness_module.routes:ROUTES`), not a file under `backend/routes/` that the plugin's manifest reached out to: the package is self-contained (a distribution that leaves the plugin out has no dead router in the wheel), and the backend no longer imports the plugin's private modules to serve it. Builtin routers keep their absolute prefixes (`/api/...`); third-party plugins live under `/api/x/<id>` — the one deliberate difference, recorded in docs/API_POLICY.md.
