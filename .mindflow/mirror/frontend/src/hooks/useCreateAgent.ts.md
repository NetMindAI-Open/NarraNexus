---
code_file: frontend/src/hooks/useCreateAgent.ts
last_verified: 2026-08-27
stub: false
---

## 2026-08-27 — 乐观插入的那行补 `bound_channels: []`

`AgentInfo.bound_channels` 变必填后,这里手工拼的 `newAgent` 也得填。新建的 agent
确实没有渠道绑定,所以 `[]` 是真值;下一次 `refreshAgents()` 会用服务端投影覆盖
整行。**这一行是"乐观 UI"而不是真实响应**——`api.createAgent` 的 `res.agent` 里
并没有 `bound_channels`/`agent_framework`/`model` 这些目录字段,所以刚建完的 agent
在 Dashboard 目录里那几列会短暂显示 `—`,直到下一次刷新。这是已知且可接受的,
不要为了消掉它在这里编造 framework/model 的默认值(会和服务端算出来的生效值不一致)。

# useCreateAgent.ts — shared "create a blank agent" action

## Why it exists

Agent creation was triggered from two places — the sidebar `AgentList`
button and the `OnboardingChecklist` card (retired 2026-08-19; the
auto-provisioned guide agent carries onboarding now, so AgentList is the
only remaining UI caller). Before this hook the logic lived only in
`AgentList.handleCreateAgent`; duplicating it into the checklist would
have let the two drift (forget `setActiveAgent`, forget the onboarding
side effect, etc.). The hook stays the single create path.

## What it owns

1. `api.createAgent` call — accepts optional `{ teamId }` (#43); when a
   `teamId` is provided, the call passes it through to the backend and, on
   success, refreshes the teams store so the new agent appears under that team
   immediately without a full agents-list reload.
2. Store wiring — prepend to `configStore.agents`, set it active in both
   `configStore` (agentId) and `chatStore` (setActiveAgent, clears badge)
3. Onboarding side effect — fires `markOnboardingStep('first_agent_created')`
   fire-and-forget on success

## Design decisions

**Onboarding mark is mode-agnostic + best-effort.** It fires in local
mode too (cheap, harmless — only the checklist *card* is cloud-gated) and
its failure is swallowed so it can never block or error agent creation.

**Stores read via `getState()`, not hook subscriptions.** `createAgent` is
a `useCallback` with an empty dep array; reading the stores imperatively
inside keeps the callback stable and avoids stale-closure bugs.
