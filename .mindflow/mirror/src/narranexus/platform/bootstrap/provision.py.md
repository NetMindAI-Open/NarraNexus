---
code_file: src/narranexus/platform/bootstrap/provision.py
stub: false
last_verified: 2026-09-04
---

## 2026-08-10 (PR-6) — `_SAFE_AGENT_ID` 兜底：agent_id 路径穿越防护

`agent_id` 会成为文件系统路径段（`agent_workspace_path` = base/{user_id}/{agent_id}）
和 DB 键。`provision_new_agent` 入口先 `_SAFE_AGENT_ID.match`（`^[A-Za-z0-9_-]+$`），
不匹配直接 raise（先于 add_agent / workspace 创建），阻断 `../victim/agent` 式跨租户
写入。放在这唯一 seam 是因为三个调用方（auth 路由、create_agent MCP 工具经
DirectStore、social create-agent 路由）都汇入此处——铁律 #5 治根因。路由层另有
`pattern=^agent_[0-9a-f]{12}$` 更紧的 pydantic 前置。

## Why it exists

The canonical "make a brand-new agent usable" sequence, extracted as one
shared seam (PR-2 pre-open review #3). It existed as THREE drifting copies —
auth.py's create_agent route (complete, the semantic source), the
create_agent MCP tool closure (a HALF copy that skipped default-skill
install), and PR-2's own social-network create-agent route. The MCP closure's
gap meant agent-created agents shipped with no marketplace skills and, before
PR-2, no social/basic-info/bus instances and no peer-discovery entry — the
"ask agent X came back empty" P1.

## The sequence

add_agent → InstanceFactory.create_agent_level_instances → sync_agent_discovery
→ apply_bootstrap(profile) → SkillMarketplaceService.install_defaults
(fire-and-forget; the step review #3 named as missing from the MCP copy) →
seed caller-supplied awareness onto the factory-built AwarenessModule instance.
Each step past add_agent folds its failure into a warnings list rather than
raising — EXCEPT peer-discovery (step 2), which is deliberately bare so it
bubbles to the caller's own try (auth.py's historical behaviour). The agent
row exists either way and per-turn hooks re-sync.

## Convergence

All THREE call sites now delegate to this seam: auth.py's create_agent route,
the create_agent MCP tool closure, and the social-network create-agent HTTP
route. auth.py stays the SEMANTIC SOURCE (the seam mirrors what its route
established) and keeps only the non-shared parts — user-existence validation,
team assignment (#43), and the CreateAgentResponse shape — outside the seam
call.

## 上下游

- 调用方: [[_social_mcp_tools]] create_agent 闭包、[[social_network]] 路由
  create-agent 端点
- 依赖: [[agent_repository]] / InstanceFactory / agent_discovery_sync /
  [[profiles]] apply_bootstrap / SkillMarketplaceService
- 语义来源: [[auth]] create_agent 路由(已改调 seam,仅留 team #43 + response)

## 2026-08-19 — `bootstrap_ctx_extra` 透传

新增 keyword-only 参数 `bootstrap_ctx_extra: Optional[dict]`，原样进
`BootstrapContext.extra` 供 profile 渲染（onboarding profile 的
persona_key / topic_index / is_local）。动机：场景调用方此前只能
`bootstrap_profile="none"` 供给后再自己 apply 真 profile——中间态短暂持久化
空 greeting，且第二次 apply 失败会被吞成永久哑巴 Agent。默认 None，四个既有
调用点（auth 路由 / social-network 路由 / MCP create_agent / onboarding）
零行为变化。

## 2026-09-04 · awareness by role (batch 5b.2)

Step 5 seeds awareness onto `ensure_role_instance(agent_id, "awareness")` (created from the module's declaration if step 1 failed); fails loud when no module declares the role.
