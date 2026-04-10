---
code_file: src/xyz_agent_context/module/skill_module/_skill_mcp_tools.py
last_verified: 2026-04-10
---

# _skill_mcp_tools.py — SkillModule MCP 工具定义

## 为什么存在

从 `skill_module.py` 分离出来，把 MCP 工具注册逻辑独立维护。提供三个工具：`skill_save_config`（保存技能的 API Key）、`skill_list_required_env`（查询技能需要哪些 env 以及配置状态）、`skill_save_study_summary`（保存技能学习摘要）。

## 上下游关系

- **被谁用**：`SkillModule.create_mcp_server()` 调用 `create_skill_mcp_server(port)` 返回 FastMCP 实例；`ModuleRunner` 部署该实例
- **依赖谁**：`_get_skill_module(agent_id, user_id)` 辅助函数动态 import 并实例化 `SkillModule`；工具方法调用 `SkillModule.set_skill_env_config()`、`get_required_env_config()`、`save_study_summary()` 等实例方法

## 设计决策

**无状态模式**：与 JobModule、SocialNetworkModule 类似，工具函数不持有全局状态，而是在每次调用时通过 `_get_skill_module(agent_id, user_id)` 创建临时的 `SkillModule` 实例（只涉及文件系统操作，不需要 DB）。这里的 `get_db_client_fn` 不需要——SkillModule 是纯文件系统操作，不涉及数据库。

**工厂函数签名简化**：`create_skill_mcp_server(port)` 只接受 `port` 参数，没有 `get_db_client_fn`（其他 Module 都有）。这是 SkillModule 无数据库依赖的直接体现。

**`skill_save_config` 是凭证管理工具**：docstring 明确列出了"何时必须调用"——任何获得 API Key 或 Token 的场景都必须调用，即使技能也在本地保存了凭证文件。原因是：本地文件是技能运行时读取的，`skill_save_config` 是让系统知道这个凭证存在（用于前端展示和运行时注入环境变量）。两者服务于不同目的。

## Gotcha / 边界情况

- **动态 import 防止循环导入**：`_get_skill_module()` 里用 `from xyz_agent_context.module.skill_module.skill_module import SkillModule` 动态 import，而不是在文件顶部。这是为了避免 `_skill_mcp_tools.py` → `skill_module.py` → `_skill_mcp_tools.py` 的循环引用（`skill_module.py` 的 `create_mcp_server` 里 import 了 `_skill_mcp_tools`）。

## 新人易踩的坑

- `skill_save_study_summary` 工具接受 `summary: str`（Markdown 格式），写到 `skills/<skill_name>/STUDY.md`。这个文件目前只是静态文档，不被 `hook_data_gathering` 自动加载到系统提示里——Agent 需要主动 `cat skills/<skill_name>/STUDY.md` 才能读到它。如果想让 Agent 每次执行时都能看到学习摘要，需要在 `_scan_skills()` 里把 STUDY.md 内容也注入 `ctx_data`。
