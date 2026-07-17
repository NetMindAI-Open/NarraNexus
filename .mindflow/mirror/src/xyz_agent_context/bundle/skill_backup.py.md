---
code_file: src/xyz_agent_context/bundle/skill_backup.py
last_verified: 2026-07-14
stub: false
---

# skill_backup.py — skill 归档机制 (subproject 2 §8.12.2-5)

## 为什么存在

Bundle export 想以"URL 安装"或"Zip 安装"方式分享 skill 时，必须能 reproduce 接收方的 skill 安装过程。这要求 install 时就把"原始来源"（GitHub URL 或上传的 zip 文件）归档到一个稳定位置，并在数据库登记。skill_backup.py 是这套归档机制的中心。

## 上下游关系

- **被谁用**：
  - `backend/routes/skills.py` — `install_skill` 路由完成时调 `backup_after_api_install`，自动归档
  - `module/skill_module/_skill_mcp_tools.py` — 4 个 MCP backup tools (`skill_backup_from_github`, `_from_md`, `_from_local_zip`, `skill_list_unbackedup`) 给 agent 自己用
  - `bundle/builder.py` — Export 时通过 `SkillArchiveRepository` 间接读 `archive_path`
- **依赖谁**：
  - `repository/skill_archive_repository.py` — DB 层
  - `httpx` — 下 GitHub tarball

## 设计决策

### 双触发路径（PRD §8.12.2）

| 来源 | 触发 |
|---|---|
| API 安装（上传 zip / 填 GitHub URL） | install_skill 路由末尾自动调 |
| Agent 自己装的（不走 API） | Agent 自觉调 backup MCP tool |

### GitHub 用 tarball，不用 git clone（PRD §8.12.4）

`https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.tar.gz` 一次 HTTP 即可，不依赖 git binary（铁律 #9 精神）。

私有仓库 v1 不支持。

### 归档目录

`~/.nexusagent/skill_archives/{user_id}/{skill_name}.zip` 或 `.tar.gz`。

> ⚠️ **SINGLE-WORKER ASSUMPTION**：`archive_path` DB 列存的是绝对本地 fs 路径。多 pod 部署时，pod A 装的 skill，pod B export bundle 时 archive_path 找不到文件。修复方式：换 S3 / 共享 volume。
>
> 详见 `.mindflow/project/references/scaling_assumptions.md` §2。

### `archive_local_zip` 安全校验

只允许 zip 路径在 caller agent 的 workspace 内（防越权）+ 必须含 SKILL.md。

## Gotcha

- 同名 skill 多次上传 → 后写覆盖前写（`upsert`）。前一次的 archive zip 文件被同名 zip 覆盖，sha256 也更新。这是 PRD 6.1.b "保留最新" 的实现方式。
- `pending` sha256：用户通过 `/api/bundle/skills/archives/upload` 提供 GitHub URL 但还没真正下载时，row 的 sha256 会先填 `"pending"`。export 走到这条 archive 时会失败 / 跳过。这是 v1 简化（不立即下载），未来要做 lazy download。

## 2026-07-10 — 排除内置技能

- `list_unbackedup` 用 `_dir_is_builtin`（读 `.skill_meta.json` 的 `builtin`）过滤掉内置技能——它们随 app 出厂，不是用户数据，不该出现在"待备份"列表里。目标机首次运行会自动重新物化。

## 2026-07-14 — `_dir_is_builtin` 去重

- 原本这里自带一份 `_dir_is_builtin`，和 `skill_module.py` 逐字重复、会漂移。现改为 `from .skill_secrets import dir_is_builtin as _dir_is_builtin`——判定逻辑收敛到 [[skill_secrets.py]] 单一真相源，本文件行为不变（仍是 `list_unbackedup` 的内置过滤器）。
