---
code_file: backend/routes/agents_rag.py
last_verified: 2026-04-10
stub: false
---

# agents_rag.py — RAG 文件上传与状态路由

## 为什么存在

GeminiRAGModule 需要把文件上传到 Google Gemini 的 Files API，然后在 Agent 推理时用 File URI 引用这些文件。这个路由负责接收前端上传的文件、存到本地临时目录、触发异步上传到 Gemini，并提供状态查询（用于前端轮询上传进度）。

## 上下游关系

- **被谁用**：`backend/routes/agents.py` 聚合；前端 RAG 文件管理面板
- **依赖谁**：
  - `xyz_agent_context.module.gemini_rag_module.rag_file_service.RAGFileService` — 所有实际文件操作的服务层：保存、状态更新、触发 Gemini 上传、列表、删除
  - `xyz_agent_context.utils.file_safety` — 文件名清理和大小检查
  - `backend.config.settings.max_upload_bytes` — 上传大小限制

## 设计决策

**两阶段上传**

前端调用上传接口时，服务器立即：1）把文件写到本地磁盘，2）把状态标记为 "pending"，3）返回 200。然后异步在后台（`asyncio.create_task`）触发 `RAGFileService.upload_to_gemini_store`。前端通过轮询列表接口查询状态，直到变为 "completed" 或 "failed"。

这样设计的原因是 Gemini Files API 的上传可能需要几秒到几十秒，同步等待会阻塞前端请求超时。异步触发让前端立刻得到响应，用轮询替代长等待。

**格式白名单**

只接受 `.txt`、`.md`、`.pdf` 三种格式，在 `sanitize_filename` 里通过 `allowed_extensions` 参数强制验证。Gemini Files API 支持的格式更多，但这里只开放了文本和 PDF，其他格式需要显式添加白名单。

**删除不同步到 Gemini**

删除接口只删本地文件和状态记录，不调用 Gemini 的文件删除 API。注释里明确说明了原因：Gemini Files API 不支持删除（截至文件创建时）。如果 Google 后来支持了删除，需要在 `RAGFileService.delete_file` 里补充调用。

## Gotcha / 边界情况

- **后台任务不能被等待**：`asyncio.create_task` 启动的上传任务如果失败，异常只会打印到日志，不会传播到请求处理器。前端需要通过轮询状态接口来发现失败。
- **本地文件和 Gemini 状态的一致性**：如果后台上传成功后本地文件被手动删除，或者状态文件损坏，列表接口可能返回不一致的信息。`RAGFileService` 负责维护这个一致性。
- **Gemini API Key 缺失**：如果 GeminiRAGModule 没有配置 API key，后台上传任务会失败，状态会停在 "pending"，前端轮询永远看不到 "completed"，也看不到明显的错误提示。

## 新人易踩的坑

RAG 文件的本地存储路径和 Agent 工作区文件（`agents_files.py`）的路径是分开的，由 `RAGFileService` 内部管理，不是 `{base_working_path}/{agent_id}_{user_id}/`。不要混淆这两套文件系统。
