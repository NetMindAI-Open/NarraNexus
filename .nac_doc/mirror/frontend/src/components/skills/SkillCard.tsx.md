---
code_file: frontend/src/components/skills/SkillCard.tsx
last_verified: 2026-04-10
---

# SkillCard.tsx — Display card for one installed skill with action buttons

Shows skill name, description, version, env config warning, study status
(studying / failed / completed with result preview), and action buttons
(Study, Configure, Enable/Disable, Remove).

## Upstream / downstream

- **Upstream:** `SkillInfo` type, action callbacks from `SkillsPanel`
- **Used by:** `SkillsPanel` list

## Design decisions

The "Study" button label becomes "Re-study" when `study_status === 'completed'`
so users know they can re-trigger learning after updating a skill's docs.

The `Configure` button only appears when `skill.requires_env.length > 0`.
When env vars are unconfigured, it shows an orange warning banner that is
also clickable to open the config dialog.

Study result (`study_result`) is rendered as Markdown via the shared `Markdown`
component — skill docs may contain headers and lists.

## Gotchas

`isStudying` is controlled externally by `SkillsPanel` (the parent tracks
which skill name is being studied). The card also checks `skill.study_status
=== 'studying'` locally as a fallback for the initial render before the
parent's state catches up.
