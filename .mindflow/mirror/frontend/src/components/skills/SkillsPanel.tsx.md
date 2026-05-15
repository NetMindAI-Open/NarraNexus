---
code_file: frontend/src/components/skills/SkillsPanel.tsx
last_verified: 2026-05-14
---

# SkillsPanel.tsx — Orchestrator for skill management, install dialogs, and MCP servers

Owns the skill list query, the two install modals (GitHub / zip), the env
config modal, the study-status polling, and (since 2026-05-14) embeds
`[[MCPManager]]` as a second section.

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
