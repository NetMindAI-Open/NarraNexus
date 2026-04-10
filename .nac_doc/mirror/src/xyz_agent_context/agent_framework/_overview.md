---
code_dir: src/xyz_agent_context/agent_framework/
last_verified: 2026-04-10
stub: false
---
# agent_framework/ — LLM 适配层与 provider 管理

## 目录角色

这个目录是 NexusAgent 与各 LLM provider 之间的隔离层，实现架构原则"不强依赖某一个 Agent 框架或 LLM"。它对上层（`agent_runtime/`、`narrative/`、各 module）暴露统一接口，让换 provider 不需要改动业务逻辑。

## 关键文件索引

- **`api_config.py`**：所有 LLM 配置的唯一入口，含 ContextVar per-task 隔离和 `set_user_config()` 多租户机制
- **`xyz_claude_agent_sdk.py`**：主 agent loop 适配器，驱动 Claude Code CLI 子进程，处理流式输出
- **`openai_agents_sdk.py`**：helper LLM 调用（决策、提取、分析），支持结构化输出 + think-block fallback
- **`gemini_api_sdk.py`**：Gemini 原生 SDK 适配，专用于 PDF 文件上传和多模态推理
- **`output_transfer.py`**：Claude SDK 消息格式 → 统一事件字典的无状态转换层
- **`provider_registry.py`**：本地单机的 `~/.nexusagent/llm_config.json` 管理，支持 5 种 provider card
- **`user_provider_service.py`**：云端多租户的 per-user provider/slot 管理，存储在数据库
- **`model_catalog.py`**：静态模型元数据（维度、token 上限）和各 provider 默认模型列表
- **`prompts.py`**：`xyz_claude_agent_sdk.py` 用到的几个 prompt 常量（chat history 格式、截断警告）
- **`evermemos_sync.py`**：将 slot 配置单向同步到 EverMemOS 的 .env 文件
- **`llm_api/`**：向量化子模块（`embedding.py` 和 `embedding_store_bridge.py`）

## 和外部目录的协作

- `agent_runtime/` 在 `run()` 入口调用 `get_agent_owner_llm_configs()` 和 `set_user_config()`，是 provider 配置进入执行路径的门户
- `narrative/` 包通过 `openai_agents_sdk.py`（helper LLM 决策）和 `llm_api/embedding.py`（向量化）间接使用这里的配置
- `backend/routes/` 中的 provider 管理 API 路由直接使用 `provider_registry.py` 和 `user_provider_service.py`
- `schema/provider_schema.py` 定义了 `LLMConfig`、`ProviderConfig`、`SlotConfig` 等数据模型，`agent_framework/` 大量使用但不定义这些 schema
