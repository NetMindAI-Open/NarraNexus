---
code_dir: src/xyz_agent_context/module/skill_module/
last_verified: 2026-04-10
---

# skill_module/ — Agent 技能扩展系统

## 目录角色

SkillModule 让 Agent 通过文件系统安装和使用"技能"（Skills）。每个技能是一个目录（`skills/<skill-name>/`），包含 `SKILL.md`（操作手册）、脚本、配置文件等。Agent 在 `hook_data_gathering` 时扫描 `skills/` 目录，把已安装技能的表格注入系统提示，告诉 LLM 当前可用哪些工具。

这是唯一一个通过文件系统而非数据库管理状态的 Module——技能本身存在磁盘，配置（API Keys 等环境变量）通过 MCP 工具写入到工作空间的配置文件，运行时自动注入到 Agent 进程的环境变量里。

SkillModule 是 `ALWAYS_LOAD_MODULES` 成员之一（见 `_module_impl/loader.py`）——它跳过 LLM 实例决策，每次执行都以合成虚拟实例（`instance_id="skill_default"`）的形式强制注入，保证技能列表总是可见。

端口 7806。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `skill_module.py` | Module 主体：扫描 `skills/` 目录（包含无 SKILL.md 的目录）；生成 Instructions；管理技能 env 配置的读写；MCP 服务器委托 |
| `_skill_mcp_tools.py` | MCP 工具：`skill_save_config`、`skill_list_required_env`、`skill_save_study_summary` |

## 和外部目录的协作

- **`agent_runtime/`**：执行前从 `ctx_data.extra_data["skill_env_vars"]` 取出技能配置的环境变量，注入到子进程（Claude Agent 的工作空间）的环境变量里——这是 `skill_save_config` 保存的凭证真正生效的时刻
- **`settings.base_working_path`**：所有技能目录都在 `{base_working_path}/{agent_id}_{user_id}/skills/` 下，Agent 的 cwd 是 `{base_working_path}/{agent_id}_{user_id}/`，所以技能路径相对于 cwd 就是 `skills/`
