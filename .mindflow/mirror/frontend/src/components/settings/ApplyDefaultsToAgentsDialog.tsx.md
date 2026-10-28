---
code_file: frontend/src/components/settings/ApplyDefaultsToAgentsDialog.tsx
last_verified: 2026-08-26
stub: false
---

## 2026-08-26 — 保存默认后「应用到所有 agent」确认框

从 [[ModelDefaultsSettings]] 保存 owner 默认成功后弹出（仅当旗下 agent 存在
覆盖）。按槽（agent / helper_llm）勾选，每槽显示受影响 agent 数；某槽零覆盖
则 checkbox 置灰。确认 = 调 `api.applySlotsToAgents(slots)`（clear-to-inherit，
删除这些 agent 的 per-agent 覆盖，使其回到继承新默认）；「仅保存默认」= 只
关闭、不动 agent。文案明示对运行中 agent 下次解析生效、不打断当前运行（守
铁律 #14/#15，平台不做打断源）。复用 `@/components/ui` 的 `Dialog`
（`isOpen`/`onClose`/`title`/`size`）+ `DialogContent`/`DialogFooter`。

**2026-08-27 auto-review 修正**：新增 `dirtySlots` prop——只渲染用户这次实际
改过的槽（helper-only 改动绝不把主模型槽摆出来可勾选，避免误删）。加一行
「你共有 N 个 agent」（`total_agents`，知情同意）。`willClear` 改用 i18next
原生插值 `t(key,{n})`，不再手写 `.replace('{{n}}')`。全部文案已注册 i18n key
（en + zh 全量）。

**2026-08-27 (r2)**：`dirtySlots` 收成 `Array<'agent'|'helper_llm'>` 联合类型
（拼错编译期报错，不再静默渲染空框）；无可选槽时 `return null` 兜底第二个
调用方。
