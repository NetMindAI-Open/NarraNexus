---
code_file: src/xyz_agent_context/module/skill_module/skill_module.py
last_verified: 2026-04-10
---

# skill_module.py — SkillModule 主体

## 为什么存在

让 Agent 知道自己装了哪些技能（扫描 `skills/` 目录），并在每次执行前把技能列表和工作空间规则注入系统提示。同时提供 API 让 Agent 可以保存技能的 API Key（通过 `set_skill_env_config()`），并在执行时把这些 Key 注入到进程环境变量里（通过 `get_all_skill_env_vars()`）。

## 上下游关系

- **被谁用**：`_module_impl/loader.py` 的 `ALWAYS_LOAD_MODULES` 列表确保它总是以 `skill_default` 虚拟实例加载；`HookManager` 调用 `hook_data_gathering`；`AgentRuntime` 从 `ctx_data.extra_data["skill_env_vars"]` 读取环境变量注入子进程
- **依赖谁**：文件系统（`settings.base_working_path`）；`SkillInfo` schema；`_skill_mcp_tools.create_skill_mcp_server`

## 设计决策

**技能状态用文件系统表达**：与其他 Module 用数据库表存状态不同，SkillModule 完全依赖文件系统——技能的存在靠目录结构，配置靠 `.skill_config.json` 文件，元数据靠 `.skill_meta.json`。这让技能可以手动安装（复制目录）、备份（zip 打包）、移植（复制到另一台机器），不需要数据库迁移。

**`ALWAYS_LOAD_MODULES` 的虚拟实例**：SkillModule 不需要 LLM 决策是否加载（不像 JobModule 需要实例决策）。`_module_impl/loader.py` 里 `ALWAYS_LOAD_MODULES = ["SkillModule"]`，强制注入 `instance_id="skill_default"` 的合成实例。这个虚拟 `instance_id` 在 `hook_after_event_execution` 里是安全的——SkillModule 没有实现该 hook，不会因空 `instance_id` 出问题。

**`WORKSPACE_RULES` 是指令与提示词共用的常量**：工作空间规则（所有文件必须在 `skills/<skill-name>/` 下、凭证必须调用 `skill_save_config` 保存等）既用于系统指令模板，也用于技能学习时的提示词。提取为常量是为了保持一致性——修改工作空间规则只需改一处。

**扫描包含无 SKILL.md 的目录**：`_scan_skills()` 不只扫描有 `SKILL.md` 的标准技能目录，也扫描只有 `.skill_meta.json` 的目录（Agent 自行创建的技能）。这支持了 Agent 自主学习和创建新技能的场景，而不仅限于从 ClawHub 安装的标准技能。

## Gotcha / 边界情况

- **`skills_dir` 可能是 `None`**：如果实例化 `SkillModule(agent_id=..., user_id=None)`，`skills_dir` 为 `None`，`_scan_skills()` 直接返回空列表。MCP Server 的工具函数也通过 `_get_skill_module(agent_id, user_id)` 实例化，如果没有 `user_id` 就没有技能目录。

## 新人易踩的坑

- `skill_env_vars` 在 `ctx_data.extra_data["skill_env_vars"]` 里的格式是 `{KEY: VALUE}` 的扁平 dict，把所有启用技能的所有环境变量合并到一起。如果两个技能有同名的环境变量，后者会覆盖前者，不会有警告。
