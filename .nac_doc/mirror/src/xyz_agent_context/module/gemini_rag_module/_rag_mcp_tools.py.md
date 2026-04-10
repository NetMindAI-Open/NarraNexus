---
code_file: src/xyz_agent_context/module/gemini_rag_module/_rag_mcp_tools.py
last_verified: 2026-04-10
---

# _rag_mcp_tools.py — GeminiRAGModule MCP 工具定义

## 为什么存在

从 `gemini_rag_module.py` 分离出来（2026-03-06 重构），把 MCP 工具注册逻辑与 Module 的核心 store 操作解耦。提供三个工具：`rag_query`（语义搜索）、`rag_upload_file`（上传本地文件）、`rag_upload_text`（上传文本内容）。

## 上下游关系

- **被谁用**：`GeminiRAGModule.create_mcp_server()` 调用 `create_rag_mcp_server(port, GeminiRAGModule)` 返回 FastMCP 实例；`ModuleRunner` 部署该实例
- **依赖谁**：`GeminiRAGModule` 类引用（通过 `module_cls` 参数传入，调用其静态方法）；`RAGStoreRepository`（`rag_query` 里通过它找到当前 Agent 的 store_id）

## `agent_id` 如何传入

工具层面要求显式传入 `agent_id`。`rag_query` 和 `rag_upload_*` 都通过 `agent_id` 定位到正确的 Gemini store。LLM 从系统提示里读取 `agent_id` 传入（Awareness Module 的指令里包含 `Your agent_id is {agent_id}`）。

## 设计决策

**`rag_query` 是同步函数**：与系统内其他 MCP 工具（通常是 `async def`）不同，`rag_query` 是同步的 `def`，直接调用 `module_cls.query_store()`（Gemini SDK 是同步的）。FastMCP 会在线程池里运行它，但如果并发量大，这会成为瓶颈。

**`rag_upload_file` 的文件路径安全**：工具接受 `file_path` 参数，LLM 可以传入任意路径。这是潜在的路径遍历风险。目前靠 Agent 的工作空间沙箱（cwd 限制）和 `RAGFileService.sanitize_filename()` 缓解，但没有明确的路径白名单校验。

**`rag_query` 的查询优化提示**：工具 docstring 里明确建议"把用户问题、知识库关键词、答案可能出现的关键词三者组合"来构建查询。这是提升 RAG 检索质量的实践经验，写在 docstring 里让 LLM 看到并遵守。

## Gotcha / 边界情况

- **`create_rag_mcp_server(port, module_cls)` 签名**：注意这里只传了两个参数，没有 `get_db_client_fn`——因为 `GeminiRAGModule` 的 store 操作不需要 DB（store_id 从本地文件或实例属性获取），与其他 Module MCP Server 的工厂函数签名不一致，不要套用错。

## 新人易踩的坑

- `rag_upload_file` 依赖 Agent 的本地文件系统，文件必须在 Agent 的工作空间目录里（`skills/` 或 cwd）。从对话里直接上传用户数据需要先把数据写到本地再调用这个工具。
