---
code_file: frontend/src/types/skills.ts
last_verified: 2026-07-21
stub: false
---

## 2026-07-21 — Marketplace 类型(stage 7)

`SkillInfo` +`source_type`;新增 `MarketplaceSkillItem` /
`MarketplaceSearchResponse` / `MarketplaceSkillDetail` /
`MarketplaceInstallResponse` / `SkillUpdateInfo`,镜像后端
`SkillCatalogEntry.model_dump()` 与 marketplace 路由的响应形状。


# types/skills.ts — Skill domain types

## 为什么存在

Skill 管理面板与其 REST service / store 共享的一套 TypeScript 形状定义，避免各处重复声明。镜像后端 `schema/skill_schema.py`（`SkillInfo`）和 `routes/skills.py` 的响应体。

## 上下游关系

- **被谁用**：Skills 管理面板组件、`services/` 里的 skills API 封装、对应 store。
- **镜像谁**：后端 `SkillInfo`（Pydantic）+ 各 skills 路由的 response model。后端字段新增必须同步到这里。

## 设计决策

- `SkillSource` 用字符串联合（`'github' | 'zip'`），JSON 友好、省去 enum import 样板。
- `study_status` 是四态联合（`idle | studying | completed | failed`），前端据此渲染学习进度与失败态。

## Gotcha

- `builtin?: boolean`（2026-07-14 新增）：技能随 app 出厂时为 `true`。UI 据此把「删除」入口换成「禁用」——内置技能**可禁用不可删**（后端 `remove_skill` 对内置抛 `ValueError` → 400，见 [[skills.py]]），删了下次运行也会自动重新物化（见 [[skill_module.py]]）。字段可选：非内置技能不带这个键。
