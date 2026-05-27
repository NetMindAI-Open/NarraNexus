---
code_file: src/xyz_agent_context/module/skill_module/skill_module.py
last_verified: 2026-05-27
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

**工作空间规则按部署模式分叉（`WORKSPACE_RULES_CLOUD` / `WORKSPACE_RULES_LOCAL`）**：NarraNexus 同时跑在共享云端和用户自己的机器上，两种环境的约束根本不同——云端必须严格沙箱（workspace-only、禁全局安装、凭证不出技能目录），本地是用户自己机器应该放松（允许全局安装，但附带「告诉用户装了什么」的 advisory）。`_resolve_workspace_rules(ctx_data)` 在 `get_instructions` 时根据 `ctx_data.deployment_mode`（由 BasicInfoModule 填）选择一个块渲染进模板。缺省时 fallback 到云端（更严格的那份），宁可过严也不能让本地版提示意外流入云端 Agent。对应的硬约束由 `agent_framework/_tool_policy_guard.py` 在 PreToolUse hook 里强制执行（工作空间越界 / 全局安装等），两者需同步改动。

**扫描包含无 SKILL.md 的目录**：`_scan_skills()` 不只扫描有 `SKILL.md` 的标准技能目录，也扫描只有 `.skill_meta.json` 的目录（Agent 自行创建的技能）。这支持了 Agent 自主学习和创建新技能的场景，而不仅限于从 ClawHub 安装的标准技能。

**install_skill 的拒因消息必须具体可操作**：每个 ValueError 都必须告诉用户「哪里出了问题 + 应该怎么改」。例如 SKILL.md 缺失时不能只说 "SKILL.md not found"，要补上「放在 zip 根目录或者唯一的顶层子文件夹下」；超出文件数限制时要带上实际 count 和上限；超出大小时要带上 MB 单位的上限。原因：这条消息会经由 `routes/skills.py` 透传给前端的错误提示，是用户唯一能看到的反馈，不能让他们去翻 Network 才知道为什么失败。配套的服务端日志在 `routes/skills.py` 的 `_reject()` 里——拒绝点统一打 `WARNING`，留下 prod 排查的 breadcrumb。

## Gotcha / 边界情况

- **`skills_dir` 可能是 `None`**：如果实例化 `SkillModule(agent_id=..., user_id=None)`，`skills_dir` 为 `None`，`_scan_skills()` 直接返回空列表。MCP Server 的工具函数也通过 `_get_skill_module(agent_id, user_id)` 实例化，如果没有 `user_id` 就没有技能目录。
- **`skills_dir` 可能不存在于文件系统**：路径对象存在 ≠ 目录被创建。删除 agent 或新建用户后第一次调用 MCP 时，`{base_path}/{agent}_{user}/skills` 还没建。**所有遍历操作**（`_scan_skills`、`_resolve_skill_dir`、`list_skills`）都必须先守 `if not self.skills_dir or not self.skills_dir.exists()`，否则 `iterdir()` 抛 `FileNotFoundError`。

## 新人易踩的坑

- `skill_env_vars` 在 `ctx_data.extra_data["skill_env_vars"]` 里的格式是 `{KEY: VALUE}` 的扁平 dict，把所有启用技能的所有环境变量合并到一起。如果两个技能有同名的环境变量，后者会覆盖前者，不会有警告。
