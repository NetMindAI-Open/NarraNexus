---
code_file: backend/routes/agents_llm_config.py
last_verified: 2026-07-18
stub: false
---

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
Delegates writes to [[agent_slot_service]].

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
