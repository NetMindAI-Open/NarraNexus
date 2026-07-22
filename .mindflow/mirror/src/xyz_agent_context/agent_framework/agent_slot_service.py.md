---
code_file: src/xyz_agent_context/agent_framework/agent_slot_service.py
last_verified: 2026-07-18
stub: false
---

## 2026-07-18 — `actor_is_staff` 参数：netmind-only + 框架钉选双门禁

`set_agent_slot(..., *, actor_is_staff: Optional[bool])`——**keyword-only
必填，刻意无默认值**（静默 bypass 正是 manyfold 缺口的成因；漏传参数 =
`TypeError`，不是悄悄放行）。`None`（**调用点必须显式写出**）= 受信内部
调用方，不检查。两条 [[cloud_policy]] 规则在此强制，均抛
`CloudPolicyViolation`（路由映射 403）：

1. **provider 来源**：prov 加载后 `ensure_slot_provider_allowed`——云端非
   staff 只能绑 netmind 卡（吸收了旧路由级 OAuth/netmind 门禁）。
2. **框架钉选**：agent 槽的 `eff_framework != owner 默认框架` 时拒绝——
   用户级框架切换是 staff-only（providers.py），per-agent 钉不同框架等于
   同一变更走侧门。为此框架解析重构为**总是**先读 owner 的 user_slots 行
   （以前仅在 agent_framework 缺省时读；每次 agent 槽写入多一次主键查询）。

## 2026-07-09 — per-agent slot override writer

Writer/reader for the ``agent_slots`` table (the overlay itself lives in
[[resolver]]). An agent inherits its owner's user-level slots by default; this
service upserts/reads the optional per-agent overrides that let one agent pin its
own coding-agent framework + model (agent slot) and its own helper model
(helper_llm slot), independent of the owner default and of the owner's other
agents.

Why it exists as its own service (not more methods on ``UserProviderService``):
the two writers have different scopes (user vs agent) and different key columns,
but MUST enforce the same provider↔slot binding rules — so the rules live in the
shared ``user_provider_service.validate_slot_binding`` and both call it. Without
that, a per-agent override could bind an incompatible provider (e.g. a codex_cli
agent slot on an aggregator, or a helper slot on an OAuth card) and the misbinding
would only surface at agent-loop time as a cryptic NotImplementedError.

Gotchas:
- The provider must belong to the agent's OWNER (providers are user-scoped);
  ``set_agent_slot`` resolves the owner from ``agents.created_by`` and looks the
  provider up under that user.
- Only the agent slot carries a framework. For the agent slot, a per-agent
  framework (if given) is validated against; else it falls back to the owner's
  current framework.
- ``clear_agent_slot(slot_name=None)`` deletes ALL of the agent's overrides (full
  reset to inherit); a specific ``slot_name`` resets just that slot.
- Only the own-provider resolution path honours overrides; the cloud SYSTEM
  free-tier pool ignores them (fixed one-model config).
