---
code_file: src/xyz_agent_context/module/skill_module/skill_module.py
last_verified: 2026-07-21
---

## 2026-07-21(晚)— frontmatter requires 抑制 body 扫描 + platform_env_available

两个修正:1)frontmatter 显式声明 `requires.env` 时,正文大写变量扫描不再
追加(正文常提到 NETMIND_BASE_URL 这类**可选**覆盖项,被误提为必填导致
Needs Config 误报);未声明的技能保留 body 扫描 fallback。2)新公开
`platform_env_available(db, user_id) -> set`:查该 user 真的能被平台解析的
变量(NetMind 行存在才算),供 routes 层做「真实」配置状态显示——
`_parse_skill_md` 保持 fs-only 乐观判定,真伪校正在 API 层。


## 2026-07-21 — 平台可解析 env(NETMIND_API_KEY 运行时注入,stage 9)

新常量 `PLATFORM_RESOLVED_ENV = ("NETMIND_API_KEY",)` + 私有
`_resolve_platform_env(env, skills)`:hook_data_gathering 收集 skill env 后,
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
(`_skill_marketplace_impl/install_pipeline.py`),不再直接调 install_skill /
install_from_github / remove_skill;bundle 导入路径仍直接用 `install_skill(zip,
target_dir_name)`,不经 pipeline(信任来源,不需要扫描 Gate)。

## 2026-07-13 — install_skill target_dir_name + meta preservation

`install_skill(zip, target_dir_name=None)` — bundle import pins the dest folder to the manifest's known `skill_dir` instead of re-deriving it from SKILL.md frontmatter (which fell back to the extraction temp-dir basename for a frontmatter-less SKILL.md, leaving a stray `skills/tmpXXXX/`). And `_save_skill_meta` now MERGES onto the existing `.skill_meta.json` (keeps `env_config` / `study_result` / `requires` that travelled with a full_copy) instead of overwriting from scratch. Test: `tests/bundle/test_skill_import.py`.

# skill_module.py — SkillModule 主体

## 为什么存在

让 Agent 知道自己装了哪些技能（扫描 `skills/` 目录），并在每次执行前把技能列表和工作空间规则注入系统提示。同时提供 API 让 Agent 可以保存技能的 API Key（通过 `set_skill_env_config()`），并在执行时把这些 Key 注入到进程环境变量里（通过 `get_all_skill_env_vars()`）。

## 上下游关系

- **被谁用**：`_module_impl/loader.py` 的 `ALWAYS_LOAD_MODULES` 列表确保它总是以 `skill_default` 虚拟实例加载；`HookManager` 调用 `hook_data_gathering`；`AgentRuntime` 从 `ctx_data.extra_data["skill_env_vars"]` 读取环境变量注入子进程
- **依赖谁**：文件系统（`settings.base_working_path`）；`SkillInfo` schema；`_skill_mcp_tools.create_skill_mcp_server`

## 设计决策

**技能状态用文件系统表达**：与其他 Module 用数据库表存状态不同，SkillModule 完全依赖文件系统——技能的存在靠目录结构，配置靠 `.skill_config.json` 文件，元数据靠 `.skill_meta.json`。这让技能可以手动安装（复制目录）、备份（zip 打包）、移植（复制到另一台机器），不需要数据库迁移。

**`ALWAYS_LOAD_MODULES` 的虚拟实例**：SkillModule 不需要 LLM 决策是否加载（不像 JobModule 需要实例决策）。`_module_impl/loader.py` 里 `ALWAYS_LOAD_MODULES = ["SkillModule"]`，强制注入 `instance_id="skill_default"` 的合成实例。这个虚拟 `instance_id` 在 `hook_after_event_execution` 里是安全的——SkillModule 没有实现该 hook，不会因空 `instance_id` 出问题。

**工作空间规则按部署模式分叉（`WORKSPACE_RULES_CLOUD` / `WORKSPACE_RULES_LOCAL`）**：NarraNexus 同时跑在共享云端和用户自己的机器上，两种环境的约束根本不同——云端必须严格沙箱（workspace-only、禁全局安装、凭证不出技能目录），本地是用户自己机器应该放松（允许全局安装，但附带「告诉用户装了什么」的 advisory）。`_resolve_workspace_rules(ctx_data)` 在 `get_instructions` 时根据 `ctx_data.deployment_mode`（由 BasicInfoModule 填）选择一个块渲染进模板。缺省时 fallback 到云端（更严格的那份），宁可过严也不能让本地版提示意外流入云端 Agent。对应的硬约束由 `agent_framework/_tool_policy_guard.py` 在 PreToolUse hook 里强制执行（工作空间越界 / 全局安装等），两者需同步改动。

**扫描包含无 SKILL.md 的目录**：`_scan_skills()` 不只扫描有 `SKILL.md` 的标准技能目录，也扫描只有 `.skill_meta.json` 的目录（Agent 自行创建的技能）。这支持了 Agent 自主学习和创建新技能的场景，而不仅限于从 ClawHub 安装的标准技能。

**内置技能物化（`_materialize_builtin_skills`）**：把 `BUILTIN_SKILLS_DIR`（`= Path(__file__).parent / "builtin_skills"`）里的 vendored 技能 `copytree` 到 workspace `skills/<name>/`，`.skill_meta.json` 打 `builtin: true`（`source_type="builtin"`）。**两个触发入口**：`hook_data_gathering` 顶部（运行时）和 `list_skills` 顶部（读时）。为什么两处都要——物化只在 `hook_data_gathering` 会导致「新建、从未运行的 agent」打开 Skills 面板看不到内置技能（`GET /api/skills` → `list_skills` → `_scan_skills` 不物化）；所以 `list_skills` 也物化一次，保证 API/UI 首次即可见。副作用刻意不放在 `_scan_skills`（保持 scan 纯只读，它被 backup 等多处调用）。幂等性判据是 **disable-aware**：`skills/<name>/` 或 `skills/.disabled/<name>/` 任一存在即跳过——否则用户禁用（move 到 `.disabled/`）后每轮被复活。`_scan_skills` / `_parse_skill_md` 都从 `.skill_meta.json` 回填 `SkillInfo.builtin`。`remove_skill` 对内置技能抛 `ValueError`（`_dir_is_builtin` 同时查 live 与 `.disabled/` 目录），路由层 `routes/skills.py` 把它翻成 400。首个内置技能是 `officecli`，其二进制由 shell/构建层预装进 PATH（见 `_overview.md`）。

**物化的并发安全（2026-07-14）**：`hook_data_gathering` 与 `list_skills` 两个入口可能并发跑物化（同一 workspace 的多协程，或多进程）。两者都通过了上面的 `.exists()` 判据后，若直接 `copytree(src, dest)` 会撞车、败者抛 `FileExistsError` 被宽 `except` 吞成 warning、掩盖真实竞态。现改为**先 `tempfile.mkdtemp` 私有暂存目录 → `os.rename` 原子换入**：`mkdtemp` 保证每个 racer 拿到唯一暂存名，`os.rename` 落到已存在的 `dest` 会干净失败（`OSError`），败者删掉自己的暂存拷贝并跳过 → 物化恰好一次、无伪 warning。暂存目录以 `.` 前缀命名，`_scan_skills` / `_builtin_skill_relpaths` 都跳过 `.` 开头目录，不会被误当技能。

**`_dir_is_builtin` 委托（2026-07-14）**：原本这里自带一份判定，和 `bundle/skill_backup.py` 逐字重复。现改为薄委托到 [[skill_secrets.py]] 的 `dir_is_builtin`，三处同源、`builtin` 语义不再漂移。

**install_skill 的拒因消息必须具体可操作**：每个 ValueError 都必须告诉用户「哪里出了问题 + 应该怎么改」。例如 SKILL.md 缺失时不能只说 "SKILL.md not found"，要补上「放在 zip 根目录或者唯一的顶层子文件夹下」；超出文件数限制时要带上实际 count 和上限；超出大小时要带上 MB 单位的上限。原因：这条消息会经由 `routes/skills.py` 透传给前端的错误提示，是用户唯一能看到的反馈，不能让他们去翻 Network 才知道为什么失败。配套的服务端日志在 `routes/skills.py` 的 `_reject()` 里——拒绝点统一打 `WARNING`，留下 prod 排查的 breadcrumb。

## Gotcha / 边界情况

- **`skills_dir` 可能是 `None`**：如果实例化 `SkillModule(agent_id=..., user_id=None)`，`skills_dir` 为 `None`，`_scan_skills()` 直接返回空列表。MCP Server 的工具函数也通过 `_get_skill_module(agent_id, user_id)` 实例化，如果没有 `user_id` 就没有技能目录。
- **`skills_dir` 可能不存在于文件系统**：路径对象存在 ≠ 目录被创建。删除 agent 或新建用户后第一次调用 MCP 时，`{base_path}/{agent}_{user}/skills` 还没建。**所有遍历操作**（`_scan_skills`、`_resolve_skill_dir`、`list_skills`）都必须先守 `if not self.skills_dir or not self.skills_dir.exists()`，否则 `iterdir()` 抛 `FileNotFoundError`。

## 新人易踩的坑

- `skill_env_vars` 在 `ctx_data.extra_data["skill_env_vars"]` 里的格式是 `{KEY: VALUE}` 的扁平 dict，把所有启用技能的所有环境变量合并到一起。如果两个技能有同名的环境变量，后者会覆盖前者，不会有警告。
