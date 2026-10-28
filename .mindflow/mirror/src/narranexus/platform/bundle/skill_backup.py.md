---
code_file: src/narranexus/platform/bundle/skill_backup.py
last_verified: 2026-10-28
stub: false
---

# skill_backup.py — skill 归档机制 (subproject 2 §8.12.2-5)

## 2026-10-28 — `archive_local_zip` 走 `resolve_inside_workspace`

`zip_file_path` 是 MCP 工具参数（agent 可控）。原来把 workspace 根 `.resolve()` 后再比较：
workspace 目录被换成指向别的租户的链接时，对方 workspace 里的 zip 会被复制进**调用者**的
`skill_archives`。现在 `_agent_workspace_root` 只 realpath base（尾段字面），`archive_local_zip`
交给 [[workspace_paths]] 的 `resolve_inside_workspace`（拒绝链接根、候选 realpath 必须落在字面根内），
ValueError 原样保留本函数的错误类型。`list_unbackedup` 仍经 `_agent_workspace_root` 列 skills 目录
名，链接 workspace 下只会暴露目录名、不读内容，本次未改。测试：
`test_skill_archive_path_safety.py::test_archive_local_zip_refuses_a_symlinked_workspace`。

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

## 2026-08-17 — SEC-07：`archive_target()` 成为唯一的归档路径构造点

`skill_archives/{user_id}/{skill_name}.*` 这个拼接原先散在 **7 处 / 3 个
文件**：本文件 4 处、[[importer.py]] 2 处、[[bundle.py]] 1 处。每一处的
`skill_name` 都来自进程外部——Form 字段（route）、bundle manifest（导入方
写的）、LLM 给的 MCP 工具参数——而全部是裸 f-string。route 那处已被 QA
实证可以 `../` 跳出用户目录写进别人的目录。

现在收敛成一个函数：

- **`archive_target(user_id, skill_name, suffix=".zip")`** —— 唯一合法构造
  点，**纯函数**（只校验和计算，不碰文件系统）。`sanitize_filename` 校验
  skill_name → `ensure_within_directory` 落地 → 再用
  `is_within_archives_root` 复查一次。最后这步不是多余的：
  `ensure_within_directory` 锚在**用户目录**上，如果用户目录本身是个指
  向树外的 symlink，结果"合规却在外面"；这里锚回 archives root，而
  symlink 出去的用户目录满足不了它。
- **`ensure_archive_dir(target)` / `prepare_archive_target(...)`** —— 建父
  目录的那一半，只在**真的要写**的时候调。拆开的理由：`archive_target`
  原先内部 `mkdir`，于是 route 里"拒绝时不建目录"的承诺只对非法
  `skill_name` 成立，另外 3 条 400 分支和整个 github 分支都会留下空目
  录——而测试参数表刚好只有非法名，绕开了唯一出问题的分支。纯化之后
  "4xx 零副作用"才是真的。
- ⚠️ **两个 containment 判据是故意不同的，不要"统一"**：写侧
  （`archive_target`）锚 archives **root**，因为它要挡的是 symlink 出逃；
  读侧（`is_within_user_archive_dir`）锚**该用户目录**，因为它要挡的是
  跨用户和 root 散装层。把写侧换成 per-user 判据会恒真（此时用户目录已
  被 resolve），symlink 那个洞会重新打开。
- `_user_archive_dir` 现在也 `sanitize_filename(user_id)`。user_id 来自
  JWT / X-User-Id 而不是表单，但"够可信了"正是 SEC-07 在下一层发生的原
  因。
- **`is_within_user_archive_dir(user_id, path)`** —— 读侧守卫。封住写路径
  **不会**回溯清理已经写坏的行（dev 库还留着 QA 那条），而 [[builder.py]]
  会把 `archive_path` 指向的文件拷进导出包，所以读的时候必须再判一次。
  **必须用 per-user 锚点**：QA 那行存的是
  `{root}/{uid}/../marker.zip`，resolve 之后在 `{root}/marker.zip`，root
  锚点判它"合规"；`{root}/{受害者}/x.zip` 同理。`is_within_archives_root`
  留着只给写侧用，它的 docstring 现在明写自己是更松的那个。
  读侧**两道锚点同时成立**才放行（per-user + root）：`base` 自己也是
  resolve 过的，所以 `{root}/{user_id}` 若是指向树外的 symlink，"在 base 里
  面"会恒真。今天写侧已经拒绝这种用户目录，读侧这一层是纵深——等哪天有第二
  条写入路径能写这一列，它就是唯一还在的网。

回归测试 `tests/bundle/test_skill_archive_path_safety.py` 里有一条 grep 式
断言：这 3 个文件中不允许再出现 `<dir> / f"...skill_name..."` 形状的行。
这类 bug 的复发方式就是有人又手拼了一次路径。

## 2026-08-17 — `backup_after_api_install` 的宽 `except` 收窄

原先是 `except Exception` + 一行 warning。这正是 [[importer.py]] zip 分支那
个**必然发生**的 `copy2(tgt, tgt)` → `SameFileError` 能长期无人发现的原因：
每次导入都抛、每次都被记成一条灰色 warning，而 `register_archive` 一次都没
执行过。

现在分两支：

- `ValueError / OSError / httpx.HTTPError` —— 环境性、消息本身就够定位的失
  败（坏 skill 名或 GitHub URL、磁盘/权限、`SameFileError` 也在 `OSError`
  这一支、tarball 下载问题），保持 warning。
- 其他一切 —— 是**这段代码自己的 bug**，用 `logger.exception` 带栈打出来。

归档仍是 best-effort（不能让归档失败把用户刚做完的安装弄失败），但
"best-effort" 不等于"静音"。对应铁律：不要为了日志干净吞异常——吞掉的前提
是"这个异常已经在别处被处理了"，这里没有。

## 2026-08-18 — `archive_local_zip` 的准入校验改调共享函数

原本自己写一段 `zipfile.ZipFile(src).namelist()` + 找 SKILL.md + 捕
`BadZipFile`。它和上传路由是 `skill_archives` 的**两个写入口**，判断标准却各
写各的、还不一致。现在两处都调 [[security.py]] 的
`validate_skill_archive_*`。

**保留的差异是故意的、且现在写在一个地方**：这条路径传
`require_skill_md=True`，因为调用方给的是自己工作区里的一个路径，"你指错文件
了"是更可能的错误、值得立刻说；上传路由不要求，免得把一份"能存但装不上"的归
档直接拒掉（那种问题由导入侧按 skill 报出来）。

顺带获得了原来没有的条目数 / 解压体积 / 加密包三道闸——这个入口以前一道都
没有。

