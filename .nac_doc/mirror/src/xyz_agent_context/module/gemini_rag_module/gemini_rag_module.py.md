---
code_file: src/xyz_agent_context/module/gemini_rag_module/gemini_rag_module.py
last_verified: 2026-04-10
---

# gemini_rag_module.py — GeminiRAGModule 主体

## 为什么存在

实现 `XYZBaseModule` 合约，提供 Agent-level 的文档存储和检索能力。每个 Agent 有一个独立的 Gemini File Search store（按 `agent_{agent_id}` 命名），Store ID 持久化在 `RAGStoreRepository` 和 `~/.nexusagent/data/` 本地文件里。

Module 本身不实现 `hook_data_gathering`——文档内容不会自动注入上下文，而是通过 MCP 工具 `rag_query` 按需检索。

## 上下游关系

- **被谁用**：`ModuleRunner` 通过 `create_mcp_server()` 启动 MCP 服务；`GeminiRAGTrigger` 的静态方法委托调用这里的核心逻辑；`backend/routes/agents.py`（RAG 文件上传 API）通过 `RAGFileService` 间接调用
- **依赖谁**：`GeminiAPISDK`（Gemini File Search API 操作）；`RAGStoreRepository`（store 映射持久化）；`_rag_mcp_tools.create_rag_mcp_server`；`~/.nexusagent/data/` 本地目录（store 映射的文件备份）

## 设计决策

**Store 是 Agent 级共享的**：一个 Agent 的所有用户共用同一个 Gemini store，没有 Narrative 级或 User 级的隔离。这是成本和复杂度的权衡——Gemini File Search API 按 store 计费，如果每个用户或每个 Narrative 各有一个 store，成本会剧增。适合存放 Agent 层面的通用知识库（产品文档、FAQ），不适合存放用户私密数据。

**Store 映射双写**：`agent_id → store_id` 的映射既写 `RAGStoreRepository`（数据库），也写 `~/.nexusagent/data/{agent_id}_rag_store.json`（本地文件）。本地文件是容灾备份——如果数据库不可用或查询失败，先查本地文件。这在开发和测试环境里很有用，但在多实例部署时本地文件的内容会不同步。

**`hook_data_gathering` 未实现**：设计上选择"不注入"而不是"总是注入文档摘要"。原因是文档库可能很大，全量注入会污染上下文；按需 RAG 查询让 LLM 只在真正需要时才检索，更节省 token。如果某些场景确实需要自动注入（比如总是加载最新产品手册摘要），应该在 Awareness 里描述，而不是在这里实现。

## Gotcha / 边界情况

- **`GOOGLE_API_KEY` 是必需环境变量**：`GeminiAPISDK` 初始化时需要 `GOOGLE_API_KEY`。如果没有这个变量，Module 实例化时不会报错，但任何工具调用都会失败。`rag_query` 的错误会传回 LLM，LLM 可能会误以为没有文档而不是 API Key 缺失。

## 新人易踩的坑

- MCP 工具 `rag_query` 是同步函数（非 `async`），直接调用 `module_cls.query_store()`。Gemini API 底层是同步调用，放在异步 FastMCP 服务器里会阻塞事件循环——这是已知的技术债，目前靠 FastMCP 的线程池来缓解。
