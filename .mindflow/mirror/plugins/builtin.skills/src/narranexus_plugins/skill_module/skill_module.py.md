---
code_file: plugins/builtin.skills/src/narranexus_plugins/skill_module/skill_module.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28 — skill_name 只能是单个目录名

`remove_skill` / `disable_skill` / `enable_skill` 入口先过 [[workspace_paths]] 的
`safe_id_segment`，不安全的名字抛 ValueError：`..` 会把 `skills_dir / ".."` 指到 workspace 根，
remove 就是 rmtree 整个 workspace。影响只限调用者自己，但必须堵。路由层另有 404
（[[routes]]），这里是给 MCP 工具等直接调用方的兜底。

`_resolve_skill_dir` **不抛**：名字可能来自 agent 自己写的 SKILL.md frontmatter（`org/pdf`），
抛错会让 env 注入对所有 skill 一起失败。不安全的名字只是**跳过直接拼接**
`skills_dir / name`，仍走 frontmatter 扫描（只会返回 `iterdir` 出来的真实子目录）；扫描不中
就返回 None。测试在 skills_dir 上一层放了一个真实的 `pdf-tools/SKILL.md`，
`_resolve_skill_dir("../pdf-tools")` 返回 None 才证明直接拼接确实被跳过。

## 2026-10-28 — `fetch_github_repo` 的 git 参数卫生

`branch` 是用户输入、直接上 git 命令行。现在克隆前过 `_validate_git_branch`：只收
`[A-Za-z0-9._/-]`，拒以 `-` 开头（选项注入，如 `--upload-pack=...`）、含 `..`（ref 穿越）、
空串、空白与 shell 元字符；不合法直接 `ValueError`，git 不会被调用。命令行在位置参数前加
`--`，url / dest 永远不会被当成选项。克隆失败不再把 git stderr（可能带解析出的主机 / 内部
细节）拼进返回给用户的错误，只回固定文案，stderr 进日志。三个调用方（自身
`install_from_github`、marketplace InstallPipeline、bundle importer）共用这一处。
测试：`tests/skill_module/test_github_skill_layouts.py`。

## 2026-09-09（复审 I6/M5/M7）— 每仓 skill 数上限；未知 skill 返回 None

`MAX_SKILLS_PER_REPO = 20`：`find_skill_roots` 超过即 ValueError（落盘前），因为 `skill_install` 是 agent
可调用的 MCP 工具，一个几百目录的仓会打满工作区磁盘并长时间占住线程；错误文案单独一条，提示装 fork/子集。
`get_skill_requirements` 对不存在的 skill 返回 **None**（不是空列表），让工具能说"没装"。
`_parse_skill_md` 兜底 description 改成一行三元：有 SKILL.md 但无 frontmatter → ""（既有约定），无 SKILL.md →
meta description 或 "(No SKILL.md found)"（meta 里空串也落占位符，与旧的 `.get(key, default)` 不同——空串
描述在列表里等于没有描述）。测试：`test_github_skill_layouts.py::test_more_than_max_skills_per_repo_…`、
`test_skill_required_env_single_source.py::test_directory_without_skill_md_is_still_listed_from_its_meta`。

## 2026-09-09 — GitHub 安装复用 `find_skill_roots`，支持多 skill 仓（GitHub #95）

`fetch_github_repo` 过去硬编码 `<clone>/SKILL.md`，而 zip 路径早就用 `_find_skill_root` 往下找一层——
于是 `my-skill/SKILL.md` 或 agent-skills / 插件仓惯用的 `skills/<name>/SKILL.md` 布局从 GitHub 装一律
报 "SKILL.md not found"。现在两条路径共用新的公开 `find_skill_roots(dir) -> [Path]`（根 SKILL.md →
只此一个；否则 `<name>/SKILL.md` 与 `skills/<name>/SKILL.md` 各算一个，跳过点目录，按名排序 R4d），
`_find_skill_root` = 首个（zip 单 skill 契约不变）。签名变化：`fetch_github_repo -> ([roots], url)`，
`install_from_github -> [SkillInfo]`（多 skill 仓逐个 `install_from_dir`，同一 source_url）；
没有任何 SKILL.md 时 ValueError 带 URL/分支与 `SKILL_LAYOUT_HINT`（三种布局的一句话说明，zip 拒绝
消息也引用它）。消费方同步：InstallPipeline / SkillMarketplaceService.install_from_url 返回列表，
routes install 与 MCP `skill_install` 逐条汇总，bundle importer 按 manifest 名挑对应项。
测试：`tests/skill_module/test_github_skill_layouts.py`（stub 掉 `git clone` 子进程，其余全真）。

## 2026-09-09 — `get_skill_requirements` 与 UI 同源（GitHub #115）

`get_skill_requirements(skill_name)` 过去只读 `.skill_meta.json["requires"]`——那是 study 步骤才写的
字段——于是"装了但没 study"的 skill 对 `skill_list_required_env` MCP 工具永远答"no required env"，
而 Skills 面板（`_parse_skill_md` → `SkillInfo.requires_env`：frontmatter ∪ body 扫描兜底 ∪ meta）
明明列着变量。两份真相现在收敛为一份：`get_skill_requirements` 走 `_resolve_skill_dir` 后调用同一个
`_parse_skill_md`，返回 `{"env": [...], "bins": [...]}`（未知 skill → **None**，见上一条；不抛）。
顺带：`_parse_skill_md` 对**不存在**的 SKILL.md 走 meta-only 兜底（不再打 "Failed to parse" warning，
description 取 meta 或 "(No SKILL.md found)"），`_scan_workspace_skills` 的无 SKILL.md 分支也改走它——
以前那个分支根本不看 meta 的 requires，也是一处分叉。测试见
`tests/skill_module/test_skill_required_env_single_source.py`。

## 2026-09-09 — `_extract_zip_safely` 把 `BadZipFile`/`OSError` 转成 `ValueError`（B-30，#113；复审 I7/I8 修订）

`zipfile.ZipFile(zip_file_path, "r")` 打不开一个非 zip 文件时抛的是
`zipfile.BadZipFile`，不是这个方法其余每一处拒绝都用的 `ValueError`。
`/api/skills/install`（source=zip）这条路由只 `except ValueError: raise
_reject(...)` 映射 400，其余异常落进 `except Exception` 变成一个不带任何
可读信息的 500——症状恰好是「上传坏 zip 返回 500」（GitHub #113）。

这条路径没有走 `bundle/security.validate_skill_archive_bytes` 那道准入门
（`backend/routes/bundle.py` 的 `/export` 路由才走那道门），`install_from_zip` →
`extract_skill_package` → `_extract_zip_safely` 直接打开文件，没有前置校验，所以这里
必须自己接住。修法：`zipfile.ZipFile(...)` 单独包一层 `except (BadZipFile, OSError)`，
转成同一种 `ValueError`。第一版只抓 `BadZipFile`；复审指出它抄的先例
`validate_skill_archive_path` 的注释明写"只抓 BadZipFile 让 OSError 漏过去"是修过一次的
坑——路径是目录（`IsADirectoryError`）、不可读、磁盘问题都是 `OSError`。**只包 open 那一行**：
`with` 块内 `zip_ref.read()` 的 OSError 是真服务端故障，不能谎报成 400。

### `zipfile.ZipFile(` 全仓扫描表（复审 I8 补全；口径 = `git grep -n "zipfile.ZipFile(" -- src/ plugins/ backend/`）

| 位置 | 处理 |
|---|---|
| `plugins/builtin.skills/.../skill_module.py::_extract_zip_safely` | 本条目转换（BadZipFile + OSError → ValueError） |
| `src/narranexus/platform/bundle/security.py` `validate_skill_archive_bytes/path` | 早已转换 → ValueError |
| `src/narranexus/platform/bundle/security.py` `scan_zip_for_sensitive` / `extract_zip_safely` | 在上两个校验之后才碰字节，已保证是合法 zip |
| `src/narranexus/kernel/plugins/install/sources.py::extract_zip` | **同批转换** BadZipFile → `SourceError`（同文件其余拒绝全是 SourceError，安装管线按它分类；此前坏的 release `backend.zip` 以裸 BadZipFile 报给用户） |
| `backend/routes/admin/logs.py::_tail_lines`（`.log.zip` 轮转日志） | 未转换，admin-only 且只读自家轮转文件；风险接受，记录在此 |
| `bundle/builder.py`、`bundle/skill_backup.py`、`marketplace/_skill_marketplace_seed.py` | 写模式（`"w"`），N/A |

## 2026-09-07 — `SKILL_METADATA_KEYS` 改从 `narranexus.contracts.openclaw` 取

不再依赖迁移子系统的 schema（复审 M7）。

## 2026-09-07（批 1 三轮复审移植）— 别名词表只有一份：`schema.migration_schema.OPENCLAW_ALIASES`

此前注释声称「与 migration detector 认的四个名字一致」是假的：detector 只认三个（无 `clawdis`）。现在
`SKILL_METADATA_KEYS` 就是 `OPENCLAW_ALIASES`，detector/extractors 从它派生。测试钉住字面元组
`("openclaw","clawdbot","clawdis","moltbot")`（不再拿常量自己参数化自己）以及三处派生关系。

## 2026-09-07 — 私有平台模块换成公开门面（批 6c，A2-1）

本文件曾 import `narranexus.platform` 的下划线私有模块。批 6c 在拥有它的包上开出了
具名公开函数（`module_system` 的 caller-identity 解析器 / `marketplace` 的
store·pipeline·secret-box / `agent_framework.llm.prompt_probe_emit` /
`agent_framework.adapters.build_tool_policy_guard`），本文件改用它们。
理由不是命名规范：这些 MCP 工具和 helper 自批 6b 起是**独立的 wheel**，
「包内私有」对它们已经不成立了。`pyproject.toml` 的
`plugins never import a private platform module` 契约（只查直接 import）守住这条线。

## 2026-09-03（批 2a.5）— `content.skills` 插件技能进技能表

`_scan_skills` 拆成 `_scan_workspace_skills`（原逻辑）+ 插件技能追加：`plugin_skills()` 给出的目录按
`_parse_skill_md` 解析，标 `source_type="plugin"`、`builtin=False`；同名以 workspace 为准（用户自己的副本或
`.disabled` 标记永不被插件遮蔽）；单个坏技能只 warning。表格里插件技能的 Path 列写真实路径（它不在
workspace 的 `skills/` 下）。没有 workspace（`user_id=None`）时也能列出插件技能。

## 2026-09-03 — frontmatter `metadata` 同时接受 `openclaw` / `clawdbot` / `clawdis` / `moltbot`

`metadata.clawdbot` 来自 OpenClaw / ClawHub 技能生态（项目改过两次名：Clawdbot → Clawdis → OpenClaw），
官方文档写明 `metadata.openclaw` 为正名、另两个为别名。原实现只读 `clawdbot`，新发布的 ClawHub
技能会门控失效（env/bins 不再被识别为必需）。现在 `_skill_runtime_requires` 按 `SKILL_METADATA_KEYS`
（新名优先）取第一个存在的块，别名词表与 `migration/detector.py` 认的四个名字一致（`moltbot` 是那边
多出的一个，预审要求两处口径统一）；`tests/skill_module/test_skill_md_metadata_aliases.py` 用同一份
SKILL.md 换键名逐个跑真实 `_parse_skill_md`。

## 2026-08-18 — `_extract_zip_safely` 的两个上限改读 [[file_safety.py]]

原先是函数内两个字面量（500 / 100MB）。它们和 bundle 侧的归档准入闸
（[[security.py]]）必须一致——门口按一个数收、安装时按另一个数拒，就是"A 接口
收下、B 接口失败"。现在两边读同一个常量，且是模块属性、调用时解析。
本文件行为不变。

## 2026-08-13 (review 轮) — 「已配置」判定统一到单一 helper

新增模块函数 `configured_env_var_names(env_config)`——「一个 var 算不算已配置」的**单一真相**(存在且可解密;吃 dict 不吃 name,故对 enabled/disabled skill 一致)。此前该规则以 `bool(env_config.get(v))` 散在 6 处、3 种语义(密文非空即算已配置)。`_parse_skill_md` 两处 env_configured 改用它→源头即诚实,hook 表格与 install_pipeline 的 config_required 只读 env_configured 故自动跟对。删掉上一版的 `get_configured_env_var_names`(按 name 解析、只认 enabled 目录,会把禁用 skill 全判未配置)。

## 2026-08-13 — 解密失败 fail-closed(2026-08-01 事故)

`get_all_skill_env_vars` 吃 3 元组:failed 的 var **跳过不注入**(skill 因缺 var 干净失败,而非拿密文跑)+ 每 skill error 日志点名需重录的 var。惰性迁移(needs_rewrite 重写 meta)在**有任何 failed 时跳过**——否则会覆盖坏值那份还能用旧 key 恢复的密文。新增 `get_configured_env_var_names(skill_name)`:只返回「存在且可解密」的 var,驱动 env_configured(见 [[skills.py]]),坏凭据不再算「已配置」。

## 2026-07-28 — R4d：所有目录遍历改为按名排序（等长重排型缓存断点）

`_scan_skills()` 的结果 → `gather` 拼出的 skills 表格 →
`ctx_data.extra_data["skills_table"]` → `contribute_instructions()` → **system prompt
可缓存前缀**。而 `Path.iterdir()` 给的是文件系统 readdir 顺序，**APFS 不是字母序**：
一个真实 workspace 实测返回 `officecli, home-assistant-setup,
netmind-transcribe, netmind-vision`。`_materialize_builtin_skills()` 每轮都跑，
agent 也可能在会话中途新建/删除技能目录，任何一次增删改名都会重排 readdir 顺序 →
**整张表格在总长度完全不变的情况下换位**。这是"等长重排"，
`[SYSPROMPT-BREAKDOWN]` 的字节计数看不见，缓存前缀在第一个换位行处断裂。

五处 `iterdir()` 全部加 `sorted(..., key=lambda p: p.name)`：

| 位置 | 为什么要排 |
|------|-----------|
| `_scan_skills()` | **主因**：直接决定 prompt 里表格行序 |
| `_materialize_builtin_skills()` | 内置技能物化顺序确定化（每轮都跑） |
| `_resolve_skill_dir()` | 两个目录 frontmatter 同名时，胜者不能取决于 readdir——它决定读写哪个 `.skill_meta.json`（env 配置 / study 状态） |
| `list_skills()` 的 `.disabled` 分支 | API/UI 面，enabled 半边已确定化 |
| `_find_skill_root()` | 压缩包内多个候选子目录时，解析出的 skill root 必须跨机器一致 |

本模块内其余可能进 prompt 的聚合都已确定：`_extract_env_vars_from_text` /
`update_requirements` 返回 `sorted(set(...))`，`requires_env` / `requires_bins`
在 `_parse_skill_md` 里也是 `sorted(set(...))`。`get_all_skill_env_vars` 的 dict
只进运行时环境变量注入，不进 prompt 文本。

测试：`tests/module/test_skill_scan_order.py`——monkeypatch `Path.iterdir`
返回置换后的列表，两种不同置换渲染出的 instructions 必须**字节相同**（且长度
相同，证明这确实是等长重排）；中途新增技能只能追加行、不得打乱既有行序。

## 2026-07-21(晚)— frontmatter requires 抑制 body 扫描 + platform_env_available

两个修正:1)frontmatter 显式声明 `requires.env` 时,正文大写变量扫描不再
追加(正文常提到 NETMIND_BASE_URL 这类**可选**覆盖项,被误提为必填导致
Needs Config 误报);未声明的技能保留 body 扫描 fallback。2)新公开
`platform_env_available(db, user_id) -> set`:查该 user 真的能被平台解析的
变量(NetMind 行存在才算),供 routes 层做「真实」配置状态显示——
`_parse_skill_md` 保持 fs-only 乐观判定,真伪校正在 API 层。


## 2026-07-21 — 平台可解析 env(NETMIND_API_KEY 运行时注入,stage 9)

新常量 `PLATFORM_RESOLVED_ENV = ("NETMIND_API_KEY",)` + 私有
`_resolve_platform_env(env, skills)`:gather 收集 skill env 后,
对「技能声明了但用户未显式配置」的平台变量,从该 user 的
`user_providers`(source=netmind, protocol=openai)行取 api_key **仅注入本次
运行的子进程环境,永不落盘**——key 轮换即时生效、cloud workspace 零额外密钥
副本。显式 skill env_config 永远优先。两处 `env_configured` 计算把平台变量视
为已配置(不再显示 Needs Config)。依赖 loader 传入的 self.db;db 缺失/无
user_id 时静默跳过。


## 2026-07-21 — SKILL_MANAGEMENT_RULES 追加进双模式 prompt(stage 5)

新常量 `SKILL_MANAGEMENT_RULES` 同文追加到 CLOUD/LOCAL 两个 workspace-rules
块(铁律 #7):禁止 Agent 手工 mkdir/rm/cp/mv 操作 `skills/` 下的技能目录,
必须走 `skill_search_marketplace` / `skill_install` / `skill_uninstall` 三个
MCP 工具(工具背后是 InstallPipeline)。这是「磁盘↔审计 DB 一致性」三道防线
中的 prompt 防线;兜底是 services/skill_sync_service.py 对账器。

## 2026-07-20 — install 原语拆分 + env_config 换 Fernet(Skill Marketplace stage 3)

安装流程拆成三个公开原语,老 API 行为不变、全量测试零回归:
`extract_skill_package(zip, dest)`(安全解包+找根)、`fetch_github_repo(url,
branch, dest) -> (root, canonical_url)`(校验+浅克隆+剥 .git)、
`install_from_dir(root, source_type, source_url, target_dir_name)`(共享尾部:
解析/替换/落盘/写 meta)。拆分动机:InstallPipeline 要在「staging 之后、落盘
之前」插入安全扫描 Gate——一体式 install_skill 做不到。`install_skill` /
`install_from_github` 现在是原语组合的薄壳。另加三个小公开件:
`merge_skill_meta` / `read_skill_meta` / `parse_skill_package`,让 pipeline
零私有访问。

`set_skill_env_config` / `get_all_skill_env_vars` 从裸 base64 改为 SecretBox
(Fernet)加解密;后者对旧 base64 值惰性迁移(读到即重写为密文)。对
routes/MCP 完全透明——env 值从不出后端,接口只返回 presence bool。

**注意**:routes/skills.py 的 install/remove 端点现在走 InstallPipeline
(`marketplace/_skill_marketplace_impl/install_pipeline.py`),不再直接调 install_skill /
install_from_github / remove_skill;bundle 导入路径仍直接用 `install_skill(zip,
target_dir_name)`,不经 pipeline(信任来源,不需要扫描 Gate)。

## 2026-07-13 — install_skill target_dir_name + meta preservation

`install_skill(zip, target_dir_name=None)` — bundle import pins the dest folder to the manifest's known `skill_dir` instead of re-deriving it from SKILL.md frontmatter (which fell back to the extraction temp-dir basename for a frontmatter-less SKILL.md, leaving a stray `skills/tmpXXXX/`). And `_save_skill_meta` now MERGES onto the existing `.skill_meta.json` (keeps `env_config` / `study_result` / `requires` that travelled with a full_copy) instead of overwriting from scratch. Test: `tests/bundle/test_skill_import.py`.

# skill_module.py — SkillModule 主体

## 为什么存在

让 Agent 知道自己装了哪些技能（扫描 `skills/` 目录），并在每次执行前把技能列表和工作空间规则注入系统提示。同时提供 API 让 Agent 可以保存技能的 API Key（通过 `set_skill_env_config()`），并在执行时把这些 Key 注入到进程环境变量里（通过 `get_all_skill_env_vars()`）。

## 上下游关系

- **被谁用**：`_module_impl/loader.py` 的 `ALWAYS_LOAD_MODULES` 列表确保它总是以 `skill_default` 虚拟实例加载；`HookManager` 调用 `gather`；`AgentRuntime` 从 `ctx_data.extra_data["skill_env_vars"]` 读取环境变量注入子进程
- **依赖谁**：文件系统（`settings.base_working_path`）；`SkillInfo` schema；`_skill_mcp_tools.create_skill_mcp_server`

## 设计决策

**技能状态用文件系统表达**：与其他 Module 用数据库表存状态不同，SkillModule 完全依赖文件系统——技能的存在靠目录结构，配置靠 `.skill_config.json` 文件，元数据靠 `.skill_meta.json`。这让技能可以手动安装（复制目录）、备份（zip 打包）、移植（复制到另一台机器），不需要数据库迁移。

**`ALWAYS_LOAD_MODULES` 的虚拟实例**：SkillModule 不需要 LLM 决策是否加载（不像 JobModule 需要实例决策）。`_module_impl/loader.py` 里 `ALWAYS_LOAD_MODULES = ["SkillModule"]`，强制注入 `instance_id="skill_default"` 的合成实例。这个虚拟 `instance_id` 在 `after_turn` 里是安全的——SkillModule 没有实现该 hook，不会因空 `instance_id` 出问题。

**工作空间规则按部署模式分叉（`WORKSPACE_RULES_CLOUD` / `WORKSPACE_RULES_LOCAL`）**：NarraNexus 同时跑在共享云端和用户自己的机器上，两种环境的约束根本不同——云端必须严格沙箱（workspace-only、禁全局安装、凭证不出技能目录），本地是用户自己机器应该放松（允许全局安装，但附带「告诉用户装了什么」的 advisory）。`_resolve_workspace_rules(ctx_data)` 在 `contribute_instructions` 时根据 `ctx_data.deployment_mode`（由 BasicInfoModule 填）选择一个块渲染进模板。缺省时 fallback 到云端（更严格的那份），宁可过严也不能让本地版提示意外流入云端 Agent。对应的硬约束由 `agent_framework/adapters/_tool_policy_guard.py` 在 PreToolUse hook 里强制执行（工作空间越界 / 全局安装等），两者需同步改动。

**扫描包含无 SKILL.md 的目录**：`_scan_skills()` 不只扫描有 `SKILL.md` 的标准技能目录，也扫描只有 `.skill_meta.json` 的目录（Agent 自行创建的技能）。这支持了 Agent 自主学习和创建新技能的场景，而不仅限于从 ClawHub 安装的标准技能。

**内置技能物化（`_materialize_builtin_skills`）**：把 `BUILTIN_SKILLS_DIR`（`= Path(__file__).parent / "builtin_skills"`）里的 vendored 技能 `copytree` 到 workspace `skills/<name>/`，`.skill_meta.json` 打 `builtin: true`（`source_type="builtin"`）。**两个触发入口**：`gather` 顶部（运行时）和 `list_skills` 顶部（读时）。为什么两处都要——物化只在 `gather` 会导致「新建、从未运行的 agent」打开 Skills 面板看不到内置技能（`GET /api/skills` → `list_skills` → `_scan_skills` 不物化）；所以 `list_skills` 也物化一次，保证 API/UI 首次即可见。副作用刻意不放在 `_scan_skills`（保持 scan 纯只读，它被 backup 等多处调用）。幂等性判据是 **disable-aware**：`skills/<name>/` 或 `skills/.disabled/<name>/` 任一存在即跳过——否则用户禁用（move 到 `.disabled/`）后每轮被复活。`_scan_skills` / `_parse_skill_md` 都从 `.skill_meta.json` 回填 `SkillInfo.builtin`。`remove_skill` 对内置技能抛 `ValueError`（`_dir_is_builtin` 同时查 live 与 `.disabled/` 目录），路由层 `routes/skills.py` 把它翻成 400。首个内置技能是 `officecli`，其二进制由 shell/构建层预装进 PATH（见 `_overview.md`）。

**物化的并发安全（2026-07-14）**：`gather` 与 `list_skills` 两个入口可能并发跑物化（同一 workspace 的多协程，或多进程）。两者都通过了上面的 `.exists()` 判据后，若直接 `copytree(src, dest)` 会撞车、败者抛 `FileExistsError` 被宽 `except` 吞成 warning、掩盖真实竞态。现改为**先 `tempfile.mkdtemp` 私有暂存目录 → `os.rename` 原子换入**：`mkdtemp` 保证每个 racer 拿到唯一暂存名，`os.rename` 落到已存在的 `dest` 会干净失败（`OSError`），败者删掉自己的暂存拷贝并跳过 → 物化恰好一次、无伪 warning。暂存目录以 `.` 前缀命名，`_scan_skills` / `_builtin_skill_relpaths` 都跳过 `.` 开头目录，不会被误当技能。

**`_dir_is_builtin` 委托（2026-07-14）**：原本这里自带一份判定，和 `bundle/skill_backup.py` 逐字重复。现改为薄委托到 [[skill_secrets.py]] 的 `dir_is_builtin`，三处同源、`builtin` 语义不再漂移。

**install_skill 的拒因消息必须具体可操作**：每个 ValueError 都必须告诉用户「哪里出了问题 + 应该怎么改」。例如 SKILL.md 缺失时不能只说 "SKILL.md not found"，要补上「放在 zip 根目录或者唯一的顶层子文件夹下」；超出文件数限制时要带上实际 count 和上限；超出大小时要带上 MB 单位的上限。原因：这条消息会经由 `routes/skills.py` 透传给前端的错误提示，是用户唯一能看到的反馈，不能让他们去翻 Network 才知道为什么失败。配套的服务端日志在 `routes/skills.py` 的 `_reject()` 里——拒绝点统一打 `WARNING`，留下 prod 排查的 breadcrumb。

**凭证 fail-closed + 单一真源（2026-08-13，round 3/4 收口）**：三个模块级函数分工——`configured_env_var_names(env_config)` 是「某 env 是否已配置」的**唯一真源**（present ∧ 可解密 ∧ **解出来非空**），**解密委托给 [[secret_box]] 的 total `decrypt_env_config`**（状态查询与注入两条路共用同一个解密器），再在状态侧过滤空明文：`{name for name, value in plain.items() if value}`。**为什么要这层真值过滤**：`encrypt("")` 是合法 Fernet token、`decrypt` 成功返回 `""`——scrubbed-bundle 还原 + legacy 迁移会留下这种形状，「可解密」≠「可用」，空凭据必须读作未配置（否则卡片绿、运行期注入空 key、opaque 失败，8/1 事故的空串版本）。真值过滤**只放状态侧**：注入路径的 `plain` 要拿去做 legacy 回写 `encrypt_env_config(plain)`，从 plain 删 key 等于迁移时静默删字段；空明文也**不能计入 `failed`**（会翻转 `if not failed` 卡住真正需要迁移的 legacy 值）。函数入口保留一层 `isinstance(env_config, dict)` 快速返回（省掉对垃圾输入调 `get_secret_box()`），解密净化仍单点在 `decrypt_env_config`。`get_secret_box()` 失败降 `logger.debug`（进程级事实、不 per-skill 刷屏），唯一带上下文的 ERROR 留给注入路径。历史上这个判定散落六处（list/detail/MCP/hook/install/enrich）导致只改一处修不干净；现全部改调它。
`env_config_status(requires_env, env_config)` 是 `_parse_skill_md` **两个 return** 的单一来源，一次算出 `(env_configured, env_platform_assumed)`：前者=必填 var 全部「自存可解密 ∨ 属 `PLATFORM_RESOLVED_ENV`」；后者=其中「属平台且**非自存**」的子集，随 `SkillInfo` 带给 API 层做 DB 校验（见 [[skills.py]] 的 enrich 反向假阴性修复）。自存的平台 var 被排除出 assumed，永不被误降。
`get_all_skill_env_vars`：`get_secret_box()` 现包 try——key 配错时 fail-closed 返回 `{}`（注入空、不抛出 gather、不拖垮整个 agent 的 skills 贡献），并发 ops ERROR。其余用 `decrypt_env_config` 三元组 `(plain, needs_rewrite, failed)`——解不开或损坏（非 str）的值**永不注入**、`failed` 里点名重录；任一 failed 时**跳过 legacy 迁移**（避免覆写还能用旧钥恢复的密文）。`PLATFORM_RESOLVED_ENV = ("NETMIND_API_KEY",)`。

**meta 读取统一走 `_load_meta_dict`（2026-08-13 round 4，🟢5）**：`.skill_meta.json` 是 agent 可写文件，可能缺失、非 JSON、或是合法 JSON 但**非 object**（数组/字符串/数字）——后者 `json.loads` 成功但随后的 `.get()` 抛 `AttributeError`。`_parse_skill_md` 的一串 `.get()` 在 frontmatter `try` **之外**，会一路抛穿 `_scan_skills` → `list_skills` → 列表 500 / hook 整份贡献丢失（正是「malformed meta 不得 crash」契约要挡的 blast radius）。新增静态 `_load_meta_dict(meta_file)` 把三处读点（`_parse_skill_md`、`_read_skill_meta`、`_scan_skills` 里无 SKILL.md 的分支）统一成「读→非 dict 一律 `{}` + 点名 warning」。非 dict 的坏 meta 本就无可恢复字段，`{}` 兜底不丢信息、且留 breadcrumb；不是静默降级。

## Gotcha / 边界情况

- **`skills_dir` 可能是 `None`**：如果实例化 `SkillModule(agent_id=..., user_id=None)`，`skills_dir` 为 `None`，`_scan_skills()` 直接返回空列表。MCP Server 的工具函数也通过 `_get_skill_module(agent_id, user_id)` 实例化，如果没有 `user_id` 就没有技能目录。
- **`skills_dir` 可能不存在于文件系统**：路径对象存在 ≠ 目录被创建。删除 agent 或新建用户后第一次调用 MCP 时，`{base_path}/{agent}_{user}/skills` 还没建。**所有遍历操作**（`_scan_skills`、`_resolve_skill_dir`、`list_skills`）都必须先守 `if not self.skills_dir or not self.skills_dir.exists()`，否则 `iterdir()` 抛 `FileNotFoundError`。

## 新人易踩的坑

- `skill_env_vars` 在 `ctx_data.extra_data["skill_env_vars"]` 里的格式是 `{KEY: VALUE}` 的扁平 dict，把所有启用技能的所有环境变量合并到一起。如果两个技能有同名的环境变量，后者会覆盖前者，不会有警告。


## 2026-08-18 — owner 工具改名跟随

`send_message_to_user_directly` 拆成 `reply_owner`（回答刚说话的 owner）与 `notify_owner`
（未被问就主动告知）。两者行为相同但纪律相反，合成一个工具就要求模型每轮自己判断该用哪种
register。本文件里改到的是该 handler 注册的 `user_reply_tool_names` / 相关文案 —— 一两行，
但 registry 条目是**活的行为**：它决定哪些工具调用算作这个来源的一次回复，也是
`render_origin_declaration` 取 label 的同一条记录。规范解释见
[[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.

## 2026-09-04 · declares its platform metadata (batch 5b)

`get_config()` is static and carries what the platform used to table about this module (default / base / always-load membership, instance prefix, role, display, decision metadata as applicable).

## 2026-09-04 · declares its discovery / long-run flag (batch 5d)

`discovery_hidden` (awareness / basic info / message bus) or `long_running_instances` (skills) moved from platform lists into `get_config()`.
