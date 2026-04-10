---
code_dir: src/xyz_agent_context/module/gemini_rag_module/
last_verified: 2026-04-10
---

# gemini_rag_module/ — 基于 Gemini File Search API 的文档检索模块

## 目录角色

GeminiRAGModule 让 Agent 具备长期文档记忆——把任意文本或文件上传到 Google Gemini File Search 存储空间，在执行时通过语义搜索检索相关内容。它是 Agent-level module（每个 Agent 一个独立的 store），所有 Narrative 和用户共享同一个 store。

与其他 Module 的重要区别：这个 Module **没有** `hook_data_gathering` 实现——Agent 不会在每次对话时自动加载文档摘要，而是通过 MCP 工具按需检索（RAG 模式）。

## 关键文件索引

| 文件 | 职责 |
|------|------|
| `gemini_rag_module.py` | Module 主体：store 管理（创建、查询、映射持久化）；RAG 的核心操作（upload, query） |
| `gemini_rag_trigger.py` | 静态工具类：把 GeminiRAGModule 的核心操作封装成不需要实例化的静态方法，供外部代码调用 |
| `rag_file_service.py` | API 层服务：临时文件管理、上传状态追踪（pending/uploading/completed/failed）、后台异步上传 |
| `_rag_mcp_tools.py` | MCP 工具注册：`rag_query`、`rag_upload_file`、`rag_upload_text` |

## 和外部目录的协作

- `repository/RAGStoreRepository`：持久化 `agent_id → store_id` 的映射，避免每次都重新创建 store；store 的本地映射也写到 `~/.nexusagent/data/` 目录下作为备份
- `backend/routes/agents.py`（或其拆分后的子模块）：调用 `RAGFileService` 处理文件上传 HTTP 请求，通过 `GeminiRAGTrigger` 把文件推送到 Gemini store
- `agent_framework/GeminiAPISDK`：底层 Gemini API 调用封装（`google-genai` SDK 包装）
