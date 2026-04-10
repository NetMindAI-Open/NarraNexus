---
code_file: frontend/src/components/skills/SkillsPanel.tsx
last_verified: 2026-04-10
---

# SkillsPanel.tsx — Orchestrator for skill management and install dialogs

Owns the skill list query, the two install modals (GitHub / zip), the env
config modal, and the study-status polling.

## Why it exists

The panel coordinates three concurrent concerns: the skill list state
(TanStack Query), the study polling loop, and the two modal dialogs. Keeping
them together avoids prop-drilling through SkillCard.

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
