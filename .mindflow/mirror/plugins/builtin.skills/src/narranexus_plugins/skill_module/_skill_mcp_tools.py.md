---
code_file: plugins/builtin.skills/src/narranexus_plugins/skill_module/_skill_mcp_tools.py
last_verified: 2026-09-09
---

## 2026-09-09 — `skill_install` 逐 skill 汇报成功与失败；`skill_list_required_env` 区分"没装"（复审 C3/M5）

`service.install_from_url` 返回列表（多 skill 仓，被拒项 `status="failed"`）：每个结果一行
（installed / already installed / `was NOT installed: <error>`），`\n` 连接（与 routes 同口径）；
marketplace id 路径包成单元素列表走同一段。`skill_list_required_env` 在 `get_skill_requirements` 返回 None
（该 agent 没装这个 skill）时答 "is not installed for this agent"，不再把拼错名字答成"无需配置"。

## 2026-09-09 — `skill_list_required_env` 不再只看 meta（GitHub #115）

工具本身没改逻辑，但它依赖的 `SkillModule.get_skill_requirements` 现在与 UI 同源
（frontmatter ∪ body 扫描 ∪ study 写入的 meta），所以没 study 过的 skill 也能正确列出
SKILL.md 里声明/提到的变量；返回值恒为 `{"env": [...], "bins": [...]}`。

## 2026-08-13 (review 轮) — 「已配置」判定统一到单一 helper

`skill_list_required_env`(agent 自查 skill 为何不工作用)的 configured 判定由 `key in env_config and env_config[key]`(密文非空即 ✓)改为 `key in configured_env_var_names(env_config)`——解不开的凭据不再对 agent 报 ✓ configured,否则 agent 会在缺 var 下继续跑 skill(8/1 失败形态的 agent 侧变体)。

## 2026-07-21 — marketplace 三工具(stage 5)

新增 `skill_search_marketplace` / `skill_install` / `skill_uninstall`。
install/uninstall 是 Agent 侧唯一被允许的技能变更路径(配合 skill_module 的
SKILL_MANAGEMENT_RULES prompt 条款),内部走 SkillMarketplaceService →
InstallPipeline,享有扫描 Gate、config 迁移与审计。`skill_install` 同时接受
marketplace id 和 GitHub URL(按前缀分流);返回文本里带 Needs-Config 提示,
Agent 应转告用户去 Skill tab 配置。桌面模式下这些工具经 cloud API 搜索/下载,
离线时返回明确的不可用消息(不抛异常打断 agent loop)。


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

- **动态 import 防止循环导入**：`_get_skill_module()` 里用 `from narranexus_plugins.skill_module.skill_module import SkillModule` 动态 import，而不是在文件顶部。这是为了避免 `_skill_mcp_tools.py` → `skill_module.py` → `_skill_mcp_tools.py` 的循环引用（`skill_module.py` 的 `create_mcp_server` 里 import 了 `_skill_mcp_tools`）。

## 新人易踩的坑

- `skill_save_study_summary` 工具接受 `summary: str`（Markdown 格式），写到 `skills/<skill_name>/STUDY.md`。这个文件目前只是静态文档，不被 `gather` 自动加载到系统提示里——Agent 需要主动 `cat skills/<skill_name>/STUDY.md` 才能读到它。如果想让 Agent 每次执行时都能看到学习摘要，需要在 `_scan_skills()` 里把 STUDY.md 内容也注入 `ctx_data`。

## `skill_list_required_env` 的配置判定必须与全局同源（2026-08-13）

「某 env 是否已配置」这一判定散落过六处（list/detail/MCP/hook/install/enrich），是历史 bug 温床。现统一到 [[skill_module]] 模块级 `configured_env_var_names(env_config)`——**唯一真源**：present ∧ 可解密。`skill_list_required_env` 里 `configured = key in configured_set or key in PLATFORM_RESOLVED_ENV`——平台可解析变量（`NETMIND_API_KEY`）走并集补上，不然平台变量会被误判未配置。密文在轮换/丢钥后解不开的值**不算已配置**（fail-closed，2026-08-01 事件），驱动前端提示重新录入而不是拿密文当凭证跑。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.
