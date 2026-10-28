---
code_file: src/narranexus/platform/agent_framework/providers/slot_service.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（PR #394 review I3）— `_is_effective_override` 删除，改用共享 `config_override_wins`

模块私有的 `_is_effective_override` 与 [[resolver]] 的覆盖判据是同一条规则的第二份副本；
现在 `count_owner_overrides` / `owner_agents_overview` 直接用 [[model_identity]] 的
`config_override_wins`，行为不变。

## 2026-08-27 (r2) — auto-review 二轮修正（编排收回 service / 审计透传 NULL）

- **批量编排收进 service**：`clear_owner_agents_slots(owner_id, slot_names)` →
  `{slot: count}`，内部先全量校验(fail-closed)+保序去重+**一次**取 agent 列表，
  再逐槽调私有 `_clear_one_slot(owner_id, slot, agent_ids)`。路由回到一行委托，
  `_owner_agent_ids` 收回**私有**，不再有「路由取 ids 传进来」的性能参数（避免
  下个批量端点照抄这个易漏形状）。**唯一公开清除入口就是复数版**——单槽薄封装
  已删（生产零调用者，留着只会成为「逐槽循环 + 每槽重取」的现成模板，铁律 #2）；
  测试直接调复数版取 `out[slot]`。
- **审计快照原样透传 NULL**（不再 `or ""`）：源行 `agent_framework`/`params_json`
  的 NULL（「继承」/「全自动」）与 `""` 语义不同，压平会毁掉恢复保真度；四个
  快照列本就可空，插 None 即写 NULL。

## 2026-08-27 — auto-review 修正（判据一致 / 审计 / 措辞诚实）

- **有效覆盖判据统一**（当时是模块级 `_is_effective_override(row)=bool(row and
  row.get("provider_id"))`，2026-09-10 起改用共享的 `config_override_wins`）：`count_owner_overrides` 和 `owner_agents_overview`
  都用它，跳过空 `provider_id` 的 framework-only stub 行——与运行时
  [[resolver]]（`_apply_agent_overrides` 跳空 provider）和 [[llm_config]]
  `_slot_view` 一致，避免同一 agent 的 chip 与 card 打架。
- **`clear_owner_agents_slot` 不套这个判据**：它按 `(agent_id, slot_name)` 删
  全部行（stub 也删——stub 本就该被清）；只有计数/展示跳 stub。删除**前**把
  行快照写进 `agent_slot_clear_audit`（不可逆批量删除的可追溯留痕，见
  [[schema_registry]]）。
- **N+1 措辞改诚实**：`owner_agents_overview` 免掉的是 **HTTP 层**（前端从 N 个
  llm-config 请求变 1 个），DB 层仍是 1(agents)+N(每 agent 一次 agent_slots)。
- **`_owner_agent_ids` 加 `fields=["agent_id"]` 投影**（agents 表有 fat
  `agent_metadata`，不再整行拉回丢弃）。（本轮曾短暂改公开供路由取，r2 已收回
  私有——见上。）
- `count_owner_overrides` 用 `{s.value:0 for s in SlotName}` 生成 per-slot 计数
  （不再硬编码两个槽名、total_agents 不再混进 per-slot 判断）。

## 2026-08-26 — owner 范围的批量继承化 + Dashboard overview

单 agent 覆盖读写之外，新增三个 **owner 范围** 方法（都用
`agents.created_by` 圈定本人 agent，跨 owner 天然隔离）：

- `count_owner_overrides(owner_id)` → `{agent, helper_llm, total_agents}`：
  Model-Defaults「应用到所有 agent」确认框的影响面预览。
- `clear_owner_agents_slot(owner_id, slot_name)`：把某槽的 per-agent 覆盖在
  owner 名下**全部**删掉（clear-to-inherit——回到继承 owner 默认，下次解析
  生效，不动运行中 loop）。`db.delete` 无 IN 语义，故逐 agent_id 等值删；
  返回真正清掉的 agent 数。
- `owner_agents_overview(owner_id)` → `{agent_id: {slot: {model, inheriting}}}`：
  喂 Dashboard 折叠行 model chip，用一次 HTTP 调用替代 per-agent 的 llm-config
  请求（DB 层仍每 agent 一次 agent_slots 查询——见 2026-08-27 修正）。

这三者支撑「改默认→一键应用到全体」与「Dashboard 就地看/改单 agent 模型」
两个前端功能；语义刻意是**清除覆盖**而非盖写快照，未来 owner 默认再变时
被清过的 agent 会自动跟随。

## 2026-07-31 — 继承到「订阅凭据 ↔ 框架」这条新规则

本文件没改代码：per-agent pin 走的还是共用的 `validate_slot_binding`，所以
[[user_service]] 2026-07-31 新增的第 2 条（CLI 订阅卡只能配它自己的 CLI 框架）
在 `set_agent_slot` 上自动生效——单个 agent 把自己 pin 成 nexus_power 再绑
Claude Code Login 卡，同样在**保存时**被拒。共用校验器的价值就在这里。

## 2026-07-29 — per-agent pin 与用户级切换问同一个问题

原来的判据是「eff_framework != owner_framework」——差异即拒。那个形状把云端
用户**挡在了策略本来允许的每一个框架之外**（他 owner 默认是 claude_code，
于是任何 pin 都算「差异」）。现在改成问 [[cloud_policy]] 的
`framework_allowed_in_cloud(eff_framework, actor_is_staff)`：门禁的对象是
**目标框架的凭据骑乘风险**，与「和 owner 是否相同」无关。

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
shared ``providers.user_service.validate_slot_binding`` and both call it. Without
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

> **2026-08-20 平台默认框架变更**: 无显式选择时的默认 agent framework 由 `claude_code` 改为 `nexus_power`（免费/默认用户跑自研 NexusPower loop；模型不变）。本文件相关默认/兜底串已随之更新。

Merged origin/dev (#382 creation studio, #383 onboarding/profile/import) on 2026-09-06; dev's changes ported onto the new `narranexus.platform` paths.

## 2026-09-07（round-2 P2-I1）— the owner-default framework literal is gone

`(owner_agent_slot or {}).get("agent_framework") or "nexus_power"` became `framework_of(owner_agent_slot)`.
The bare literal was the third disagreeing answer to "which framework": under a binding to another
plugin, `set_agent_slot` validated the card against nexus_power's protocol list (`"any"` → both
accepted) while the resolver ran the bound framework and rejected the same card at turn time.
`FRAMEWORK_LOCKED_DETAIL` became `framework_locked_detail()` (built from the frameworks that qualify).
