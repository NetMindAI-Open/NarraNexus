---
code_file: packages/narranexus-contracts/src/narranexus/contracts/skill.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-09 — `SkillWorkspace` 两个成员的返回形状改成列表（GitHub #95）

`fetch_github_repo(url, branch, temp_dir)` 现在返回 `(skill_roots: list[Path], canonical_url)`，
`install_from_github(url, branch)` 返回 `list[SkillInfo]`（永不为空：无 SKILL.md 直接 raise）。
Protocol 仍声明 `-> Any`，类型检查不会拦——**第三方实现 `SkillWorkspace` 的插件必须跟着改**，否则
`InstallPipeline.install_from_github` 的 `for root in skill_roots` 会静默错位。新的公开入口
`find_skill_roots(dir)` 定义了三种布局（根 / `<name>/` / `skills/<name>/`）与 `MAX_SKILLS_PER_REPO` 上限，
zip 与 GitHub 共用。仓内实现：`narranexus_plugins.skill_module.skill_module.SkillModule`。

## 2026-09-03（批 2a）— `content.skills` 位的契约

一个 `SKILL.md` 目录 + `kind`（plugin/marketplace/workspace/builtin）。skill 模块像扫 workspace 技能一样
扫这些目录，并把 `kind` 记进 catalog，区分插件带来的技能与市场/手写技能。

## 2026-09-04 · services + host hooks (batch 3c.6)

`SkillWorkspace` Protocol — the members the platform drives a workspace through (list/get/read/merge meta, parse/extract/fetch, install_from_dir/from_github/install_skill, remove_skill, skills_dir); `SkillModule` implements it; `skills.workspaces` returns one.
