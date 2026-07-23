---
code_file: frontend/src/components/skills/SkillsPanel.tsx
last_verified: 2026-07-21
---

## 2026-07-21 — Marketplace 入口(stage 7)

Action bar 新增「Marketplace」按钮(Store icon)→ 打开
`marketplace/MarketplaceBrowser.tsx` 对话框;onInstalled 回调 refetch 技能
列表。GitHub/Zip 安装入口保持不变,三条路后端都汇入 InstallPipeline。


# SkillsPanel.tsx — Orchestrator for skill management, install dialogs, and MCP servers

Owns the skill list query, the two install modals (GitHub / zip), the env
config modal, the study-status polling, and (since 2026-05-14) embeds
`[[MCPManager]]` as a second section.

## 2026-07-02 — fix: spinner stuck forever after study completes

`studyingSkillName` (local state set by `handleStudy`) used to be cleared
**only** in the study mutation's `onError` — a successful study never
reset it, so `isStudying` passed to `SkillCard` stayed `true` until a
manual page reload. Fixed by reading `useStudyStatus`'s polled `data` and
resetting `studyingSkillName` once `study_status` reaches a terminal
state (`completed` / `failed`).

The reset is done during render (comparing against a `seenStudyStatus`
state guard), not inside a `useEffect` — calling `setState` directly in
an effect body trips the `react-hooks/set-state-in-effect` lint rule.
This follows React's documented "adjust state when a prop changes"
pattern: compare the latest polled status to the last-seen one, and if
it differs, update both in the same render pass before commit.

## 2026-06-11 — atomic `section` prop

`section?: 'skills'|'mcp'` renders exactly one section (atomic IA);
default unchanged.

## 2026-06-10 — embedded mode

`embedded` prop drops the outer Card + duplicate title when hosted in
the bookmark drawer's [[AgentProfilePanel]]; install/refresh/show-
disabled actions stay. Default rendering unchanged.

## Why it exists

The panel coordinates three concurrent concerns: the skill list state
(TanStack Query), the study polling loop, and the two modal dialogs. Keeping
them together avoids prop-drilling through SkillCard.

## 2026-05-14 — Renamed "Skills" → "Skill & MCP", absorbed MCPManager

User request: MCP servers are a tool/capability concern and belong next to
Skills, not buried in the Config (Awareness) panel.

- `MCPManager` moved from `components/awareness/` → `components/skills/` and
  is now rendered as a bordered second section inside the panel's
  `CardContent` scroll area, below the Skills list.
- `CardContent` switched to a single outer `ScrollArea` with two `<section>`s
  (Skills / MCP Servers). The Skills error / loading / empty states no longer
  use `h-full` centering — they're compact in-section blocks now, so the MCP
  section stays reachable even when the skill list fails to load.
- Panel `CardTitle` and the right-panel tab label (`[[ContextPanelHeader]]`)
  both renamed to "Skill & MCP".

## Upstream / downstream

- **Upstream:** `useSkills` hooks (TanStack Query), `useConfigStore`
  (agentId/userId for env config), `api.getSkillEnvConfig` /
  `api.setSkillEnvConfig`
- **Downstream:** `SkillCard` (display + actions), `InstallDialog`,
  `EnvConfigDialog` (inline local component)
- **Consumed by:** right-panel tab layout

## Design decisions

**Study auto-resume:** On mount the panel checks if any skill in the list has
`study_status === 'studying'` and immediately starts `useStudyStatus` polling
for it. This makes the "Studying..." spinner appear correctly after a page
reload even if study was triggered in a previous session.

**EnvConfigDialog is inline:** The env config dialog is a private sub-
component defined inside `SkillsPanel.tsx` rather than extracted to its own
file. It's small and tightly coupled to `agentId / userId` from the panel's
scope. If it grows, extract it to `EnvConfigDialog.tsx`.

## Gotchas

`showDisabled` checkbox controls whether TanStack Query includes disabled
skills in its fetch. Toggling it triggers a new API call (the query key
includes `showDisabled`), not a client-side filter. This means the disabled
count badge only counts visible skills, not all skills.
