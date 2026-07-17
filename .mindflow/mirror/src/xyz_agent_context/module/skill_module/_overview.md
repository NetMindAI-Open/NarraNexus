---
code_dir: src/xyz_agent_context/module/skill_module/
last_verified: 2026-07-10
---

# skill_module/ — Agent 技能扩展系统

## 目录角色

SkillModule 让 Agent 通过文件系统安装和使用"技能"（Skills）。每个技能是一个目录（`skills/<skill-name>/`），包含 `SKILL.md`（操作手册）、脚本、配置文件等。Agent 在 `hook_data_gathering` 时扫描 `skills/` 目录，把已安装技能的表格注入系统提示，告诉 LLM 当前可用哪些工具。

这是唯一一个通过文件系统而非数据库管理状态的 Module——技能本身存在磁盘，配置（API Keys 等环境变量）通过 MCP 工具写入到工作空间的配置文件，运行时自动注入到 Agent 进程的环境变量里。

SkillModule 是 `ALWAYS_LOAD_MODULES` 成员之一（见 `_module_impl/loader.py`）——它跳过 LLM 实例决策，每次执行都以合成虚拟实例（`instance_id="skill_default"`）的形式强制注入，保证技能列表总是可见。

端口 7806。

## 内置技能（Built-in Skills）

技能来源除了 ClawHub / GitHub / agent 自建 / 前端上传，还有一类**随 app 出厂的内置技能**，vendored 在 `builtin_skills/<name>/`（仓库内）。`_materialize_builtin_skills()` 把每个内置技能 `copytree` 物化到 workspace `skills/<name>/`，并在 `.skill_meta.json` 打 `builtin: true`。它在**两个入口**被调用：`hook_data_gathering`（运行时）和 `list_skills`（读时/API）——后者保证「新建、从未运行的 agent」打开 Skills 面板首次即可见（否则 `GET /api/skills` 走 `_scan_skills` 看不到）。

- **为什么物化而非引用仓库路径**：Cloud 的 workspace-read-guard 禁读 workspace 外路径，物化后 `cat skills/<name>/SKILL.md` 才合法；且前端/备份机制天然可见。
- **幂等 + disable-aware**：`skills/<name>/` 或 `skills/.disabled/<name>/` 任一存在即跳过物化——否则用户禁用/删除后每轮被复活。删除对内置技能被禁止（`remove_skill` 抛 `ValueError`），闭合另一条复活路径。
- **不进用户数据**：内置技能从备份/导出中排除（`bundle/skill_backup.py` 的 `list_unbackedup`、`bundle/builder.py` 的 workspace tar），目标机首次运行会自动重新物化。
- **首个内置技能：`officecli`**（Office 文档 docx/xlsx/pptx CLI）。其二进制不由 agent 安装，而是由 shell/构建层预装进 PATH：`docker/Dockerfile.manyfold`（Cloud）、`run.sh` `_try_install_officecli`（Local）、`scripts/build-desktop.sh`（Desktop DMG，落 `resources/nodejs/bin/`）。三处版本号需同步 bump。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `skill_module.py` | Module 主体：物化内置技能；扫描 `skills/` 目录（包含无 SKILL.md 的目录）；生成 Instructions；管理技能 env 配置的读写；MCP 服务器委托 |
| `_skill_mcp_tools.py` | MCP 工具：`skill_save_config`、`skill_list_required_env`、`skill_save_study_summary` |
| `builtin_skills/<name>/` | 随 app 出厂的 vendored 内置技能（目前：`officecli/SKILL.md`）；运行时物化到 workspace |

## 和外部目录的协作

- **`agent_runtime/`**：执行前从 `ctx_data.extra_data["skill_env_vars"]` 取出技能配置的环境变量，注入到子进程（Claude Agent 的工作空间）的环境变量里——这是 `skill_save_config` 保存的凭证真正生效的时刻
- **`settings.base_working_path`**：所有技能目录都在 `{base_working_path}/{agent_id}_{user_id}/skills/` 下，Agent 的 cwd 是 `{base_working_path}/{agent_id}_{user_id}/`，所以技能路径相对于 cwd 就是 `skills/`
