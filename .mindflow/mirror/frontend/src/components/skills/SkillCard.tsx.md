---
code_file: frontend/src/components/skills/SkillCard.tsx
last_verified: 2026-07-10
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

## 2026-07-10 — built-in 展示

- `skill.builtin` 为真时：标题旁渲染 `t('skills.card.builtin')` badge；**删除按钮整段隐藏**（内置技能会重新物化，删了无意义），但 disable/enable 保留。后端 `remove_skill` 也会拒删（400），前端隐藏是 UX 层的防御。
