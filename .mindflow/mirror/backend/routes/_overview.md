---
code_dir: backend/routes/
last_verified: 2026-07-24
stub: false
---

# backend/routes/ — API 路由层

## 目录角色

`routes/` 按资源域组织：多文件的域收进子目录（2026-07-24 起——`agents/`、
`admin/`、`manyfold/`、`channels/`、`artifacts/`、`dashboard/`、`office_watch/`、
`transcription/`），单文件域留在根部。每个文件持有独立的 `APIRouter` 实例，由
`main.py` 统一注册。设计原则不变：一个文件只负责一个资源域，不引用其他路由文件的
内部实现。`marketplace_skills.py` / `marketplace_teams.py` 仍在根部（marketplace
域负责人另行调整）。

`agents/core.py`（原 `agents.py`）是纯聚合器——它本身不定义任何路由，只把 `agents/` 下的子路由聚合挂载到 `/api/agents` 前缀。agent 相关路由按资源子类型分文件（awareness、chat_history、files、mcps、social_network、cost、artifacts、attachments、bus_failures、circuit_breaker、llm_config）。

## 关键文件索引

| 文件 | 前缀 | 资源域 |
|------|------|------|
| `websocket.py` | `/ws` | Agent 运行时流式通信 |
| `auth.py` | `/api/auth` | 用户认证、Agent CRUD |
| `agents/core.py` | `/api/agents` | 聚合 agents/ 子路由 |
| `agents/awareness.py` | `/api/agents` | Awareness 读写 |
| `agents/chat_history.py` | `/api/agents` | Narrative、Event、简化聊天记录 |
| `agents/cost.py` | `/api/agents` | LLM 调用费用统计 |
| `agents/files.py` | `/api/agents` | 工作区文件管理 |
| `agents/mcps.py` | `/api/agents` | MCP URL 增删改查 |
| `agents/social_network.py` | `/api/agents` | 社交网络实体查询 |
| `jobs.py` | `/api/jobs` | Job 列表、取消、批量创建 |
| `inbox.py` | `/api/agent-inbox` | MessageBus 频道消息 |
| `providers.py` | `/api/providers` | LLM 提供商与 Slot 配置 |
| `skills.py` | `/api/skills` | Skill 安装、学习、环境配置 |

## 和外部目录的协作

所有路由文件的业务逻辑依赖都在 `src/xyz_agent_context/` 里：`repository/` 做 DB 访问，`schema/` 提供 Pydantic response models，`agent_runtime/` 提供 AgentRuntime，`module/` 提供各 Module 的服务层。路由文件只做参数接收、调用和结果组装，不直接操作数据库。
