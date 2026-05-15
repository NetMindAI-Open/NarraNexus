---
code_file: frontend/src/components/teams/TeamManagementModal.tsx
last_verified: 2026-05-08
stub: false
---

# TeamManagementModal.tsx — Team CRUD modal (subproject 1)

双栏：左是 team 列表 + 创建表单；右是选中 team 的元数据（name / color / intro_md）+ 成员勾选 + 删除按钮。

`intro_md` 编辑直接落库 `teams.intro_md`，bundle export 时复用作为默认 README.md（议题 8）。
