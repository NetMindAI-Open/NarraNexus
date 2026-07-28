---
code_file: backend/routes/agents/llm_config.py
last_verified: 2026-07-28
stub: false
---

## 2026-07-28 — 不再返回 free_tier 锁块

GET 响应里的 `free_tier: {active, model}` 块删除。免费额度是普通 provider
卡，per-agent override 不会被任何东西抢占，composer 的模型 chip 永远是可用
控件 —— 继续下发一个恒为 inactive 的锁块，只会诱导前端渲染一种不可能发生的
状态。

## 2026-07-23 — GET 响应新增 `free_tier` 锁定块（诚实化模型选择器）

GET `/{agent_id}/llm-config` 的 `data` 增加 `free_tier: {active, model}`。动机：
云端免费额度优先策略（[[resolver]] 的 SYSTEM_OK 分支）在免费额度有余额时
**忽略 per-agent override**、锁死系统固定模型——底部 [[ComposerModelBadge]] 却照常
显示可切下拉、写库、乐观更新，用户以为切成功了实则运行时永远没变。这是测试同学报的
"model 选择器切换不生效" 的真根因（不是前端/写库 bug）。

`free_tier` 由单点 `free_tier_lock_for(user_id, sys_svc, quota_svc, db)`
（[[resolver]]）算出——路由只负责从 `request.app.state` 取 system_provider /
quota_service（可能为 None）并连同 db 传进去；resolver 组装 + 是否锁定 + model 提取全在
provider_resolver 单点完成（review 抓出第一版把 helper 复制进了 llm-config 和 quota 两个
路由，本次收敛）。`active` 语义 = SYSTEM_OK 门；`model` 是锁定时真正运行的系统 agent
模型。本地模式两服务为 None → `active=False`，前端行为与改前完全一致（切换保持可用）。

前端据此：徽章渲染只读 `free tier · <model>` chip（优先于其它状态），
[[AgentLlmConfigPanel]] 顶部加诚实 banner（控件仍可编辑，允许预配置，额度耗尽即生效）。
决策背景：Owner 选了"UI 诚实化、免费期锁单模型"，不改 free-tier-first 运行时策略本身。
测试见 test_agents_llm_config_routes.py 新增三例（未 wire 服务→inactive / 有余额→
锁定系统模型 / 余额耗尽→inactive）。

## 2026-07-18 — 路由级门禁删除，策略随 set_agent_slot 下沉 cloud_policy

昨天加的内联 netmind-only 检查块（含它的 prov 查询）整体删除：PUT 现在把
`actor_is_staff=_is_staff(request)` 传给 `set_agent_slot`，策略（provider
来源 + **per-agent 框架钉选门禁**，后者是新增的——云端非 staff 不得钉与 owner
默认不同的框架，堵住框架切换 staff-gate 的侧门）在服务层由 [[cloud_policy]]
统一强制；路由 catch `CloudPolicyViolation` → 403。`is_cloud_mode` import
随之移除。

## 2026-07-17 — 云端门禁从 OAuth-only 扩宽为 netmind-only

PUT 的云端非 staff 门禁从 `source in _OAUTH_SOURCES`（拒 OAuth 卡）改为
`source != "netmind"`（只许 NetMind 卡）——产品决策：云端只能用 NetMind 账户
运行，自有 API key 是本地版功能。旧 OAuth 拦截被**包含**（OAuth 源都非
netmind），`_OAUTH_SOURCES` 常量随之删除。与 providers.py 的
`_netmind_slots_only` 同一规则（那边守用户级槽 + onboard/add）；staff 豁免、
本地不受影响。前端 [[AgentLlmConfigPanel]] 同步过滤下拉。测试见
test_agents_llm_config_routes.py 新增三例（403 / netmind 通过 / staff 绕过）。

## 2026-07-09 (review fixes) — raw owner rows + deployment_mode + owner-scoped gate

- **GET reads RAW ``user_slots`` rows**, not ``UserProviderService.get_user_config().slots``.
  ``SlotConfig`` carries only provider_id / model / thinking / reasoning_effort —
  it DROPS the ``params_json`` and ``agent_framework`` columns ``_slot_view``
  reads. Feeding ``model_dump()`` made every owner-default framework read as
  claude_code and every reasoning param read as auto, breaking inheritance for
  codex_cli owners (the panel then wrote claude_code into the override → 400).
- ``_is_cloud`` was replaced by ``deployment_mode.is_cloud_mode()`` (single
  source of truth; honours ``NARRANEXUS_DEPLOYMENT_MODE`` + treats an unset
  ``DATABASE_URL`` as local) — the local ``_is_cloud`` DB-URL sniff was a 3rd
  copy of a known-skewed impl. ``providers.py``'s copy now delegates too.
- The staff-gate provider lookup is scoped to the OWNER
  (``{user_id, provider_id}``) so it never inspects another user's row.
- Route-level tests: ``tests/backend/test_agents_llm_config_routes.py`` (owner
  codex_cli default → GET returns codex_cli; non-owner 403; override view; reset).

## 2026-07-09 — per-agent LLM config endpoints

Sub-router (mounted under ``/api/agents`` via [[agents]]) for the per-agent LLM
overrides that back the chat-page model/framework quick-switch + detailed panel.
Delegates writes to [[slot_service]].

Endpoints:
- ``GET /{agent_id}/llm-config`` — per-slot view for agent + helper:
  ``{inheriting, effective, override, owner_default}``. ``effective`` is the
  override if present else the owner default. The provider dropdown options come
  from the existing ``GET /api/providers`` (not duplicated here).
- ``PUT /{agent_id}/llm-config/{slot_name}`` — set one slot override.
- ``DELETE /{agent_id}/llm-config/{slot_name}`` — reset that slot to inherit;
  ``slot_name='all'`` clears both.

Design decisions / gotchas:
- **Ownership 403**: ``_require_owner`` asserts ``agents.created_by`` == the
  caller (``resolve_current_user_id``). Changing an agent's runtime brain/billing
  is owner-only.
- **Cloud staff-gate**: mirrors the framework-switch gate in [[providers]] — in
  cloud a non-staff caller may not bind an OAuth-source provider (it would ride
  the shared CLI credentials).
- **No hot-reload**: config is resolved per run from the DB, and
  ``set_user_config`` is ContextVar/task-scoped (can't reach a running loop), so a
  change here applies on the agent's NEXT run. The handler says so explicitly.
