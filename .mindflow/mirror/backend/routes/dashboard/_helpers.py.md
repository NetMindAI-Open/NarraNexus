---
code_file: backend/routes/dashboard/_helpers.py
last_verified: 2026-07-24
stub: false
---

# dashboard/_helpers.py — pure helpers behind GET /api/dashboard/agents-status

## Why it exists

The agents-status endpoint has to fan a single dashboard poll out into many
per-agent aggregations (last activity, live jobs, recent events, today's
metrics, in-progress module instances, enhanced signals) and then collapse
them into permission-correct response models. Keeping that logic out of the
route handler stops every endpoint from re-deriving "what is this agent doing
right now" and gives the privacy/visibility rules one enforcement point. It is
deliberately pure-ish: small sync transformers plus async DB aggregators, no
route/FastAPI coupling.

## How it works / design

- `to_response` is the privacy gate: owner sees [[_dashboard_schema]]'s
  `OwnedAgentStatus` (full detail); a non-owner of a public agent gets the
  stripped `PublicAgentStatus` with `bucket_count` fuzzing the running count;
  private-non-owned returns `None` and the route drops it. `extra='forbid'` on
  the public model is the second line of defense if a field leaks here.
- `build_action_line` / `build_run_state_for_agent` deliberately avoid stale
  `final_output` for running agents (it is null mid-run, Step-4 persistence);
  they read `instance_jobs.description` / `bus_messages.content` instead. All
  text is control-char stripped + UTF-8-codepoint truncated to 80 — XSS and
  layout-break defense even though React escapes at render.
- `classify_kind` maps `working_source` → the `AgentKind` enum that the
  frontend rail/verb logic keys off (see [[api.ts]], [[healthColors]],
  [[DashboardSummary.tsx]]); `derive_health` and `derive_attention_banners`
  pre-compute the server-driven health bucket and banners so the UI does not
  reinvent severity ordering.
- G3 stale detection: `fetch_instances` buckets in_progress module_instances
  into `active` vs `stale` (older than `STALE_THRESHOLD_SECONDS`, env-tunable),
  whitelisting genuinely long-running modules (`SkillModule`) per binding-rule
  14. Stale instances do NOT count toward running_count/kind — they only feed
  the zombie badge.
- Gotchas: every DB helper is SQLite/MySQL dual-dialect (datetime objects vs
  ISO strings normalized via `isoformat()` / dateutil), so don't assume a
  string. **`trigger` is a MySQL reserved word** — `fetch_recent_events`
  SELECTs it, so it must be backticked (`` `trigger` ``); bare, it raises
  `(1064 ...)` on MySQL and the function's `except` silently returns an empty
  feed. SQLite tolerates it bare, hiding the bug locally (fixed 2026-07-07,
  same class as [[auth]]; regression in
  `tests/backend/test_trigger_reserved_word_sql.py`). Metrics columns that
  don't exist yet (duration, token cost) emit
  `None` on purpose (frontend renders "N/A"). `fetch_jobs` partitions each live
  state independently — never re-union `pending` with active/blocked/paused or
  the route double-counts (the v2.1.1 regression). Recent-events maps
  `trigger == MESSAGE_BUS` to kind `chat` / "Group chat reply" so team group
  events don't fall through to a raw "Message_Bus" label.

## 合并自旧版镜像（2026-04-13 legacy _dashboard_helpers.md）

# backend/routes/dashboard/_helpers.py — Intent

## 为什么存在
把 `dashboard.py` 路由里所有**可纯函数化**的逻辑抽出来——便于单测、便于复用、也让 route 文件本身保持薄。

覆盖：action_line 构造、排序、kind 分类、分桶、to_response factory、4 个 async 聚合 fetcher、sparkline 查询、health 派生、humanize verb、banner 派生、recent events 整形。

**职责明确界限**：这里**只**做"形式转换 + DB 查询 + 字符串组装"，不做 HTTP、不做 auth、不做 rate limiting。Route 层负责调度。

## 上下游
- **上游**：`backend/routes/dashboard/routes.py` 的主路由 + lazy 详情路由都调这里的函数
- **下游**：
  - `xyz_agent_context.utils.db.db_factory.get_db_client` 做所有 DB 查询
  - `backend/state/active_sessions.py::get_session_registry` 只被 route 直接调用（这里不碰 registry）
  - `backend/routes/dashboard/_schema.py` 所有 Pydantic 类型
- **测试**：`tests/backend/test_dashboard_helpers.py`（纯函数）+ `test_dashboard_fetchers.py`（async DB）+ `test_dashboard_v21.py`（v2.1 additions）

## 设计决策
1. **`build_action_line` 不走 events.embedding_text**（TDR-4 + R11）：那是 Step 4 持久化产物，对**正在运行**的 event 大概率 null。改走 `instance_jobs.description` / `bus_messages.content` / `sessions[0].channel` 等实时字段。
2. **`sort_agents` 分两组**（TDR-11）：Running 组（按 started_at desc）在前，Idle 组（按 last_activity_at desc）在后。None 时间戳当作最老（空字符串 lexicographical 最小）。
3. **`humanize_verb` v2.1.2 加 `instances` 参数**：CALLBACK/SKILL_STUDY/MATRIX 三种 kind 不能再返回硬编码字符串，必须用 `module_class + description`。否则用户看不出 agent 在做什么模块。
4. **`fetch_jobs` 返回 RAW per-state lists**（v2.1.1 bug 修复）：不再有 `pending` union 字段。每个 state 的 list 互不重叠，调用方可以放心遍历所有 state 不会 double-count。
5. **`fetch_enhanced_signals.token_rate_1h` 硬返 None**：events 表当前没有 per-event token 列。要真出数字需要先扩 schema。前端遇到 null 渲染 "N/A" 而不是 0（避免误导）。
6. **`derive_health` 优先级**：error > warning > paused > healthy_running > idle_long > healthy_idle。这个顺序直接决定 rail 颜色；改顺序前看清楚 TDR-4 + 前端 HEALTH_COLORS。
7. **`bucket_count` 的范围**（0 / 1-2 / 3-5 / 6-10 / 10+）：和 `_dashboard_schema.CountBucket` Literal **必须同步**。
8. **v2.2 G3: `fetch_instances` 返回 bucketed dict**：签名从 `dict[str, list[dict]]` 改为 `dict[str, dict[str, list[dict]]]`（两层：agent_id → {active, stale}）。`active` 里的 instances 参与 `running_count` 和 `kind` 推导；`stale` 里的 instances 只传给 `stale_instances` 字段。不会再因为 zombie instance 把 agent 标成 Running。
9. **`STALE_THRESHOLD_SECONDS` 通过 env var `STALE_INSTANCE_THRESHOLD_SECONDS` 覆盖**（默认 600s）：方便测试和 ops 调整，不硬编码。
10. **`LONGRUN_MODULE_WHITELIST` 豁免 SkillModule + GeminiRagModule**：这两个 module 的 `in_progress` 实例正常情况下就会跑很久（技能训练、RAG 索引），不应该被 stale 检测误报。whitelist 是 frozenset，新增 module 需手动维护。

## Gotcha
- 所有 SQL 查询用 `%s` 占位符（MySQL 风格），`AsyncDatabaseClient.execute` 会自动翻译成 sqlite `?`。混用会崩。
- `fetch_last_activity` 返回的 `last_at` 如果后端是 MySQL，值是 `datetime` 对象不是 ISO string，**必须调用方自己 `_iso()` 归一化**（route 层已处理）。
- `derive_health` 用 `dateutil.parser` 解析 ISO 时间——如果 events.created_at 是 naive datetime（无时区），会当 UTC 处理。如果以后改 MySQL 列为带时区，要 review。
- `build_run_state_for_agent` 对 sessions 做"取第一个"——真实生产中若 session 排序需求变化（比如按最新优先），要改。
- `_LIVE_JOB_STATES` 元组顺序**不是**展示顺序，是 SQL `WHERE status IN (...)` 的参数顺序；前端展示顺序在各自的组件里。
- `humanize_verb` 对未知 `kind` 返回 `Running ({kind})` 作为最后 fallback——这意味着后端若加了新 `WorkingSource` 但忘了更新这个函数，前端会看到字面 `Running (NEW_KIND)`，不崩但难看。加 kind checklist：改这里 + StatusBadge ICON_MAP + types/api.ts `AgentKind` union。
- **`_is_instance_stale` 对 `updated_at=None` 返回 `False`（不当 stale 处理）**：DB 里 updated_at 可能为 null（旧数据）。宁可漏报 stale 也不误报，避免把正常运行的 module 标成 zombie。
- **`fetch_instances` 签名变更**（v2.2 G3 breaking change）：从 `dict[str, list[dict]]` 变为 `dict[str, dict[str, list[dict]]]`。任何直接调用这个函数的代码（包括测试）都必须更新取值方式为 `inst_map[aid]["active"]`。
