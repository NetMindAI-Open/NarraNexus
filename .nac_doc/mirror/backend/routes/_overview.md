---
code_dir: backend/routes/
last_verified: 2026-04-10
stub: false
---

# backend/routes/ — API 路由层

## 目录角色

`routes/` 目录下的每个文件对应一个资源域，各自持有独立的 `APIRouter` 实例，由 `main.py` 统一注册到应用。设计原则是每个文件只负责一个资源域的 CRUD 和操作，不引用其他路由文件的内部实现。

`agents.py` 是纯聚合器——它本身不定义任何路由，只把 7 个 `agents_*` 子路由聚合在一起挂载到 `/api/agents` 前缀下。拆分原因是 agent 相关路由数量太多，按资源子类型（awareness、chat_history、files、mcps、rag、social_network、cost）分文件管理。

## 关键文件索引

| 文件 | 前缀 | 资源域 |
|------|------|------|
| `websocket.py` | `/ws` | Agent 运行时流式通信 |
| `auth.py` | `/api/auth` | 用户认证、Agent CRUD |
| `agents.py` | `/api/agents` | 聚合以下子路由 |
| `agents_awareness.py` | `/api/agents` | Awareness 读写 |
| `agents_chat_history.py` | `/api/agents` | Narrative、Event、简化聊天记录 |
| `agents_cost.py` | `/api/agents` | LLM 调用费用统计 |
| `agents_files.py` | `/api/agents` | 工作区文件管理 |
| `agents_mcps.py` | `/api/agents` | MCP URL 增删改查 |
| `agents_rag.py` | `/api/agents` | RAG 文件上传 |
| `agents_social_network.py` | `/api/agents` | 社交网络实体查询 |
| `jobs.py` | `/api/jobs` | Job 列表、取消、批量创建 |
| `inbox.py` | `/api/agent-inbox` | MessageBus 频道消息 |
| `providers.py` | `/api/providers` | LLM 提供商与 Slot 配置 |
| `skills.py` | `/api/skills` | Skill 安装、学习、环境配置 |

## 和外部目录的协作

所有路由文件的业务逻辑依赖都在 `src/xyz_agent_context/` 里：`repository/` 做 DB 访问，`schema/` 提供 Pydantic response models，`agent_runtime/` 提供 AgentRuntime，`module/` 提供各 Module 的服务层。路由文件只做参数接收、调用和结果组装，不直接操作数据库。
