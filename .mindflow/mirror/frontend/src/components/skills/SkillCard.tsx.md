---
code_file: frontend/src/components/skills/SkillCard.tsx
last_verified: 2026-07-22
---

## 2026-07-22 — 动作行换行(4 按钮不被裁切)

带 env 需求的技能(如 netmind-transcribe)动作行是 4 个按钮
(Study · Configure/ENV · Enable/Disable · Remove),原来单行不换行,窄面板下
Remove 被卡片右缘裁掉(实测截图)。动作行加 `flex-wrap`:第 4 个按钮换到第二行,
gap-2 同时给行/列间距。无 env 的技能仍是 3 按钮单行。

## 2026-07-21 — Source 徽标(stage 7)

非 builtin 且带 `source_type` 的技能显示来源徽标(marketplace/github/zip/
manual…,mono 小标签,不走 i18n——值本身就是标识符)。数据来自
`SkillInfo.source_type`(后端 `_parse_skill_md` 从 `.skill_meta.json` 回填)。


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
