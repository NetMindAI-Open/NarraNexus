---
code_file: src/xyz_agent_context/repository/skill_archive_repository.py
last_verified: 2026-05-08
stub: false
---

# skill_archive_repository.py — SkillArchive CRUD (subproject 2)

## 为什么存在

`skill_archives` 表登记每个用户的每个 skill 的"原始来源"（GitHub URL 或上传的 zip），是 bundle export 时决定 install_method 默认值的依据。

## 上下游关系

- **被谁用**：
  - `bundle/skill_backup.py` — 4 个 backup tools 写库
  - `bundle/builder.py` — Export 时读 `archive_path`
  - `backend/routes/bundle.py` — `/api/bundle/skills/archives` GET / upload
  - `backend/routes/skills.py` — `install_skill` 完成时通过 `backup_after_api_install` 间接写库
- **依赖谁**：`AsyncDatabaseClient`、`BaseRepository`

## 设计决策

### `upsert(user_id, skill_name, ...)`

复合唯一键 `(user_id, skill_name)`（schema 里有 unique index），同名 skill 重复上传 → 后写覆盖前写。这是议题 6.1.b "保留最新归档" 的实现方式。

### `archive_path` 列

> ⚠️ 见 `.mindflow/project/references/scaling_assumptions.md` §2 — 当前是绝对本地 fs 路径，多 pod 部署需要换对象存储 URL。

## Gotcha

- 删除 skill 时**没**自动删 archive 行（议题 6 决策：归档保留），需要将来加手动清理 UI。
