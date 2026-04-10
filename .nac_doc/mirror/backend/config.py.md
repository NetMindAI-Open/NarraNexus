---
code_file: backend/config.py
last_verified: 2026-04-10
stub: false
---

# config.py — 后端集中配置

## 为什么存在

原本各路由文件里散落着 `os.getenv(...)` 和魔法数字，难以全局修改也难以追踪。`config.py` 把所有后端可调整的常量集中在一个 `Settings` 类里，所有模块只需要 `from backend.config import settings` 就能拿到类型化的配置值，不需要自己处理环境变量解析和默认值逻辑。

## 上下游关系

- **被谁用**：
  - `backend/main.py` — 读取 `settings.cors_origins` 用于 CORSMiddleware，读取 `settings.frontend_dist` 决定是否挂载前端静态文件
  - `backend/routes/websocket.py` — 读取 `settings.ws_heartbeat_interval` 控制心跳频率
  - `backend/routes/agents_files.py` — 读取 `settings.max_upload_bytes` 限制文件上传大小
  - `backend/routes/agents_rag.py` — 同上
  - `backend/routes/skills.py` — 同上
- **依赖谁**：只依赖 Python 标准库 `os` 和 `pathlib`，无外部依赖

## 设计决策

**读一次，模块级单例**

`Settings` 类在模块加载时立即读取所有环境变量，赋值给类属性（不是实例属性）。好处是读取逻辑只执行一次，应用运行期间的配置是稳定的。代价是 `Settings` 不是真正的 dataclass，改一个属性会影响模块全局状态——但这不是预期的使用方式，也不需要热重载配置。

被否决的方案：用 Pydantic `BaseSettings`（从 `pydantic-settings` 包）。它能做字段验证和类型转换，但引入了额外依赖，且项目的配置量较小，简单的类够用。如果将来配置复杂度增加（比如需要 `.env` 文件、嵌套结构），迁移到 `BaseSettings` 是合理方向。

**CORS origins 的默认值**

默认值覆盖了本地开发的所有常见来源：Vite（5173）、其他 Node dev server（3000）、FastAPI 自身（8000），以及 Tauri 的两种形式（`tauri://localhost` 和 `http://tauri.localhost`）。环境变量 `CORS_ORIGINS` 接受逗号分隔的字符串，由 `_parse_list` 函数解析为列表。云部署时通过 `CORS_ORIGINS` 覆盖这个列表，只允许生产域名。

**`frontend_dist` 的路径计算**

默认值通过 `__file__` 相对路径计算 `frontend/dist`，这使它在任意工作目录下运行都能正确找到前端构建产物，不依赖 `cwd`。Tauri dmg 部署时可以通过 `FRONTEND_DIST` 环境变量覆盖为 sidecar 里实际的路径。

## Gotcha / 边界情况

- **`Settings` 类属性赋值是模块加载时执行的**：如果在 `config.py` 被 import 之前就设置了某个环境变量，它会被正确读取。但如果在 import 之后才设置（比如测试 fixture 里），`settings` 里的值不会更新，需要重新导入或直接修改 `settings.*` 属性。
- **`max_upload_bytes` 默认 50MB**：这个限制在文件上传路由里被手动检查（`enforce_max_bytes`），而不是通过 FastAPI/Starlette 的 `LimitUploadSize` 中间件。这意味着大文件仍然会被完整读入内存，只是在读完之后才报错。如果需要在流级别拒绝大文件，需要换方案。

## 新人易踩的坑

增加新的环境变量配置时，必须在 `Settings` 类里加字段，不要在路由文件里散落新的 `os.getenv()`。否则新配置不受统一管理，测试时也难以 mock。

`settings` 是模块级单例，测试之间共享。如果一个测试改了 `settings.max_upload_bytes`，下一个测试会看到这个改动，除非有显式的 teardown 恢复。
