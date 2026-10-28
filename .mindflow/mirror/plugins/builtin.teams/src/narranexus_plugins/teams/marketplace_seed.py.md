---
code_file: plugins/builtin.teams/src/narranexus_plugins/teams/marketplace_seed.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 私有平台模块换成公开门面（批 6c，A2-1）

本文件曾 import `narranexus.platform` 的下划线私有模块。批 6c 在拥有它的包上开出了
具名公开函数（`module_system` 的 caller-identity 解析器 / `marketplace` 的
store·pipeline·secret-box / `agent_framework.llm.prompt_probe_emit` /
`agent_framework.adapters.build_tool_policy_guard`），本文件改用它们。
理由不是命名规范：这些 MCP 工具和 helper 自批 6b 起是**独立的 wheel**，
「包内私有」对它们已经不成立了。`pyproject.toml` 的
`plugins never import a private platform module` 契约（只查直接 import）守住这条线。

## 2026-08-19 — 4 bundles re-cut for the v1.17 team mechanics

briefing/marketing/web-dev/gaokao entries now point at re-cut bundles
(`-20260819` for marketing/gaokao, `-20260819c` for briefing/web-dev).
The agent awareness inside them named retired `bus_*` tools and bus
"channels"; it now names the current surface (`message_agent`, and
`message_team` only where a team-room turn is genuinely in play) and
describes mechanics accurately (a DM always wakes its recipient; room
delivery is @mention-only). The briefing pipeline dispatches
and collects over `message_agent` DMs — team-room delivery is @mention-only
and agent-to-agent @mentions are hop-capped, so a cron-driven room-based
pipeline would go deaf (pre-review C1/C2). The pipeline never touches the
room: the one-send-verb-per-turn rule keeps `message_team` off a job/DM
turn's desk anyway (C3), and the Maestro collects deliveries with a single
end-of-window `read_history` pass per analyst instead of the retired
`bus_get_unread` (C4). Ships with v1.17.0, when
prod also runs this surface. Old bundle files stay hosted so a prod build pinning
the old sha keeps seeding unchanged until then.

## 2026-07-22 — review 修复:同步 httpx→异步

`httpx.Client`(同步,在 async 里冻 loop)改 `httpx.AsyncClient` + await。


# _team_marketplace_seed.py

Bootstrap seed: 9 official team templates ported from the unmerged ee1db871
catalog. Diverges on hosting — instead of pointing at narra.nexus, it
FETCHES each `.nxbundle` once from the narra.nexus source URL (migration
input only), verifies sha256, stores it in OUR template store, and writes a
catalog row with the resulting store_key. Idempotent (skips re-upload when
the store already has the key), best-effort per-entry (one unreachable
source never aborts the rest). Runs in the backend lifespan only where the
instance IS the registry (cloud / SKILL_MARKETPLACE_LOCAL_REGISTRY).

Batch 6b.3: moved from `platform/marketplace/_team_marketplace_seed.py`; invoked by the plugin's `onDidStartBackend` hook.
