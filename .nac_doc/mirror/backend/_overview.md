---
code_dir: backend/
last_verified: 2026-04-10
stub: false
---

# backend/ — FastAPI 后端应用层

## 目录角色

`backend/` 是整个系统对外暴露的 HTTP/WebSocket 层，它不包含任何业务逻辑，只负责协议处理和路由分发。所有真实的业务逻辑都在 `src/xyz_agent_context/` 核心包里。`backend/` 里的文件主要做三件事：解析请求参数、调用核心包的 Repository 或 Service、把结果包装成 response schema 返回。

这个目录的代码量故意保持最小——任何超过一两百行的路由文件都应该考虑把逻辑下沉到核心包。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `main.py` | ASGI 应用实例、中间件注册、lifespan（DB init + auto-migrate）、前端静态文件挂载 |
| `auth.py` | JWT 生成/验证、bcrypt 密码哈希、HTTP 中间件（云/本地模式切换） |
| `config.py` | 所有后端可调整常量的统一入口，通过环境变量覆盖 |
| `routes/` | 各资源域的 APIRouter 定义，见子目录概览 |

## 和外部目录的协作

- **核心包 `src/xyz_agent_context/`**：所有路由文件都向下调用核心包的 `repository/`（CRUD）、`schema/`（Pydantic models）、`agent_runtime/`（AgentRuntime 编排器）、`module/`（SkillModule、RAGFileService 等）。`backend/` 不直接操作数据库，而是通过 `get_db_client()` 拿到连接后转给 Repository。
- **`frontend/`**：`main.py` 在启动时检查 `frontend/dist/` 是否存在，存在则挂载为静态文件并添加 SPA fallback，实现同进程服务前端。
- **Tauri desktop**：`config.py` 里的 `frontend_dist` 路径和 CORS origins 默认值都包含了 Tauri 相关的特殊值（`tauri://localhost`），以确保桌面端和 web 端行为一致。
