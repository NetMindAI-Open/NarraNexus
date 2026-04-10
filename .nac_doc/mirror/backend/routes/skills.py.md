---
code_file: backend/routes/skills.py
last_verified: 2026-04-10
stub: false
---

# routes/skills.py — Skill 安装、学习与环境配置路由

## 为什么存在

Skill 系统让 Agent 可以通过安装第三方工具包（从 GitHub 或 zip）来获得新能力。安装后 Agent 需要"学习"这个 Skill（通过 `AgentRuntime` 读取 `SKILL.md` 并执行配置步骤）。这个路由管理 Skill 的完整生命周期：安装、启用/禁用、删除、触发学习、查询学习状态、管理环境变量配置。

## 上下游关系

- **被谁用**：`backend/main.py` — `include_router(skills_router, prefix="/api/skills")`；前端 Skills 管理面板
- **依赖谁**：
  - `SkillModule` — 所有 Skill 文件系统操作（安装、列表、enable/disable、删除、env config 读写）
  - `AgentRuntime` — 学习任务里运行 Agent 来执行 SKILL.md 里的配置步骤
  - `MCPRepository` — 学习任务里加载 MCP URLs（与 websocket.py 相同的逻辑）
  - `AsyncOpenAI`（通过 `openai` 包）— `_extract_requirements_via_llm` 调用 gpt-4o-mini 解析 env var 需求

## 设计决策

**学习任务作为后台 asyncio 任务**

`study_skill` 接口立即返回 `{"study_status": "studying"}`，实际的学习过程（运行 `AgentRuntime`，可能需要几分钟）在 `asyncio.create_task` 里异步执行。前端通过 `GET /{skill_name}/study` 轮询状态。学习结果（成功/失败的摘要）通过 Agent 在 SKILL.md 指导下调用 MCP tool `skill_save_study_summary` 写回，或者在学习结束后由路由代码设置 fallback 状态。

**LLM 提取 env var 需求**

学习完成后，用 `gpt-4o-mini` 分析 SKILL.md，提取需要的环境变量（如 API keys）和二进制依赖（如 `node`、`gog`）。这比手动写 regex 更可靠，因为 SKILL.md 的格式是非结构化的自然语言。提取结果保存到 `.skill_meta.json` 的 `requires_env` 和 `requires_bins` 字段，前端用这些信息显示配置面板。

**`/{skill_name}/study` 路径优先级**

POST `/{skill_name}/study` 和 GET `/{skill_name}/study` 需要在 GET `/{skill_name}` 之前注册，否则路径匹配会有歧义。注释里说 FastAPI 用注册顺序匹配，更具体的路径优先——实际上同一路由器内 FastAPI 是按注册顺序（不是特异性）匹配的，所以这里 `/{skill_name}/study` 必须在 `/{skill_name}` 之前出现在代码里。

**zip 安装用临时目录**

zip 文件上传时先保存到 `tempfile.mkdtemp()` 创建的临时目录，解压安装后用 `finally` 块清理临时目录。这防止了磁盘空间泄露。

## Gotcha / 边界情况

- **学习中状态防重入**：如果 Agent 正在学习某个 Skill（`study_status == "studying"`），再次调用学习接口会返回 `success=False, message="Already studying"`，不会启动第二个任务。但这只是内存里的检查——如果进程重启，`study_status` 可能在文件里被持久化为 "studying"（如果没来得及更新），下次无法重新触发。
- **`_extract_requirements_via_llm` 依赖 OpenAI**：如果没有配置 OpenAI API key（`openai_config.api_key` 为空），这个函数直接返回，不会提取 env var 需求。Skills 面板里的环境变量配置界面会是空的，用户看不到需要填什么。
- **Skill 学习时 Agent 写文件到工作区**：学习提示词里明确要求 Agent 把配置文件保存到 `skills/{skill_name}/` 路径（不是 `~/` 等），但这只是提示词约束，不是技术强制。如果 Agent 不遵守，文件可能保存到意外位置。

## 新人易踩的坑

`_get_skill_module` 创建 `SkillModule` 时传 `database_client=None`。`SkillModule` 的很多操作（文件系统操作）不需要 DB，但某些操作（比如创建 Job）会需要 DB。学习任务内部调用 `AgentRuntime` 时 AgentRuntime 自己会初始化 DB，不依赖 SkillModule 的 DB 参数。如果将来 SkillModule 需要直接读写 DB，要在这里传入有效的 `db_client`。
