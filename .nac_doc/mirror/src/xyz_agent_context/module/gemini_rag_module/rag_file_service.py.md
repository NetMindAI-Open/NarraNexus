---
code_file: src/xyz_agent_context/module/gemini_rag_module/rag_file_service.py
last_verified: 2026-04-10
---

# rag_file_service.py — RAG 文件上传服务层

## 为什么存在

HTTP 文件上传是一个三阶段异步过程：前端发送文件 → 后端临时存储 → 后台上传到 Gemini。`RAGFileService` 是这个过程的编排层，把"临时文件路径管理"、"上传状态跟踪"和"后台异步上传"封装在一起，让 API 路由代码只需要调用 `service.upload_and_track()` 就能触发全流程，不需要知道具体细节。

还提供了 `convert_document_to_markdown()`（调用 `docling` 库），但目前在 `gemini_rag_module.py` 里注释掉了，暂未使用。

## 上下游关系

- **被谁用**：`backend/routes/agents.py`（或其子模块）在处理文件上传 HTTP 请求时调用；通常在 `POST /agents/{agent_id}/rag/upload` 这类端点里
- **依赖谁**：`GeminiRAGTrigger`（实际执行上传）；`utils.file_safety.ensure_within_directory` 和 `sanitize_filename`（路径安全检查）；Python 标准库的 `tempfile`/`pathlib`

## 设计决策

**临时文件目录**：上传的文件暂存在某个可配置的临时目录（`~/.nexusagent/tmp/` 或系统 temp 目录），文件名经过 `sanitize_filename()` 消毒。状态文件（JSON 格式）和临时文件放在同一目录，通过 `{upload_id}.status.json` 命名关联。

**状态机**：上传流程的状态是 `pending → uploading → completed/failed`，写在 JSON 文件里。这是一种简单的持久化状态机，让前端可以轮询 `/rag/upload/{upload_id}/status` 查询进度，而不需要 WebSocket。

**`convert_document_to_markdown` 是可选功能**：`docling` 是重依赖（需要单独安装），目前代码里注释掉了对它的调用。如果想支持 PDF/DOCX 的 Markdown 转换，取消注释并在 `pyproject.toml` 里加 `docling` 依赖。

## Gotcha / 边界情况

- **后台上传阻塞事件循环**：`background_upload_to_gemini()` 是 `async def`，但内部调用的是 `GeminiRAGTrigger` 的同步方法（Gemini SDK 是同步的）。这意味着上传大文件时会阻塞 asyncio 事件循环。如果并发上传请求量大，需要改用 `asyncio.to_thread()` 把同步调用包裹在线程池里。

## 新人易踩的坑

- 状态文件路径依赖文件系统——在 Kubernetes 等无状态部署环境里，跨 Pod 查询状态会失败（因为状态文件在不同 Pod 上）。如果需要水平扩展，应该把状态改存到数据库或 Redis 里。
