---
code_file: src/xyz_agent_context/module/gemini_rag_module/gemini_rag_trigger.py
last_verified: 2026-04-10
---

# gemini_rag_trigger.py — Gemini RAG 文档操作静态工具类

## 为什么存在

尽管名字叫"Trigger"，这个类和 `job_trigger.py` 的后台轮询 Trigger 完全不同——它是一个纯静态工具类（无实例，无后台循环），把 `GeminiRAGModule` 里的文档操作（上传、查询）封装成可以在不实例化 Module 的情况下直接调用的静态方法。

存在的理由：`RAGFileService` 和 `backend/routes/agents.py` 需要在 HTTP 请求处理路径里触发文档上传，但这些地方不在 Module 生命周期里，没有 `GeminiRAGModule` 实例。`GeminiRAGTrigger` 提供了这个"无需实例化"的操作接口。

## 上下游关系

- **被谁用**：`RAGFileService.background_upload_to_gemini()` 在后台异步上传时调用 `GeminiRAGTrigger.upload_text()` 或 `upload_file()`；`backend/routes/agents.py` 可能直接调用 `GeminiRAGTrigger.query()` 做 API 查询
- **依赖谁**：`GeminiRAGModule`（委托调用模块的静态方法）

## 设计决策

**委托而不是复制**：`GeminiRAGTrigger` 里所有方法最终都委托给 `GeminiRAGModule` 的对应静态方法，没有独立的业务逻辑。它是一个"别名层"，让调用方不需要 import `GeminiRAGModule` 就能做文档操作。如果 `GeminiRAGModule` 的接口变了，这里也要同步更新。

**与 `JobTrigger` 命名冲突的认知成本**：系统里有两种"Trigger"——`JobTrigger` 是后台轮询服务（有 `start()` 方法，需要独立进程），`GeminiRAGTrigger` 是静态工具类（没有 `start()`，不需要独立进程）。命名不统一可能让新人误以为 GeminiRAGTrigger 也需要单独启动。

## Gotcha / 边界情况

- **没有异步方法**：所有静态方法都是同步的，和底层 `GeminiAPISDK` 保持一致。在 `async def` 里调用时需要用 `asyncio.to_thread()` 或接受阻塞事件循环。`RAGFileService.background_upload_to_gemini()` 是 async 函数但在里面直接调用同步方法，这是已知的阻塞风险。

## 新人易踩的坑

- `GeminiRAGTrigger` 在文件顶部直接 `from xyz_agent_context.module.gemini_rag_module.gemini_rag_module import GeminiRAGModule`——这意味着 import `GeminiRAGTrigger` 时会立刻 import `GeminiRAGModule`，触发所有 Gemini SDK 相关的依赖检查。如果运行环境没有安装 `google-genai`，import 时就会报错，而不是在调用方法时才报错。
