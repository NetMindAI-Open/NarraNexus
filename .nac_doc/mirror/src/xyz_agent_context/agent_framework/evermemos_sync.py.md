---
code_file: src/xyz_agent_context/agent_framework/evermemos_sync.py
last_verified: 2026-04-10
stub: false
---
# evermemos_sync.py — 把 LLM slot 配置单向推送到 EverMemOS .env

## 为什么存在

EverMemOS 是一个独立进程，通过读取 `.evermemos/.env` 决定使用哪个 embedding 模型和 LLM。当用户在 NexusAgent 的 Settings 页面切换 provider 或 model 后，EverMemOS 并不知道这件事。这个文件负责把 NexusAgent 的 `helper_llm` 和 `embedding` slot 配置"翻译"成 EverMemOS 能理解的环境变量，并合并写入 `.evermemos/.env`，EverMemOS 下次重启时自动生效。

## 上下游关系

上游调用者是 `backend/routes/` 中处理 provider/slot 变更的 API 路由，每次用户保存 provider 配置后触发 `sync_evermemos_from_config(config)`，传入最新的 `LLMConfig` 对象（来自 `provider_registry` 或 `user_provider_service`）。

下游是 `.evermemos/.env` 文件本身。函数只写 `LLM_*` 和 `VECTORIZE_*` 前缀的键，通过先读后合并的方式保留用户手动配置的基础设施项（数据库地址、Milvus 端口等）。

`_find_evermemos_dir()` 的三级搜索路径（`PROJECT_DIR` env var → CWD → 源码目录上三级）确保桌面端 dmg 打包模式、`uv run` 开发模式、Docker 容器模式都能找到正确位置。

## 设计决策

**VECTORIZE_DIMENSIONS 硬编码为 1024**：EverMemOS 使用 Milvus 存向量，Collection schema 在创建时固定维度。如果用户切换到不同维度的 embedding 模型（如 3072d 的 text-embedding-3-large），Milvus 会报 `SchemaNotReadyException`。截断到 1024 是为了让 schema 永远稳定，牺牲了维度精度换取了模型可切换性。注释里有完整解释。

**VECTORIZE_PROVIDER 写死为 "deepinfra"**：这是 EverMemOS 内部的 OpenAI-compatible client 标识符，名字来自历史遗留，实际行为就是通用 OpenAI 协议客户端。改成其他名字需要改 EverMemOS 代码。

**单向推送，不读回**：同步是单向的，NexusAgent 不从 EverMemOS 读取任何状态。EverMemOS 的实际运行状态不反映在 NexusAgent 的 UI 中。

## Gotcha / 边界情况

- 如果 `.evermemos` 目录不存在，函数返回 `False` 并 debug log，不报错——这是正常情况（用户没安装 EverMemOS 组件）。
- 函数是同步 I/O（`Path.write_text`），在 asyncio context 中频繁调用应包在 `asyncio.to_thread`。目前调用频率低（每次 Settings 保存才触发），暂无问题。
- `RERANK_PROVIDER` 仅在第一次写入时设置为 `"none"`（已存在则不覆盖），用户手动配置的 rerank 不会被清掉。

## 新人易踩的坑

- 这个文件只在 NexusAgent 侧有效；EverMemOS 需要重启才会读取新的 .env。如果测试时发现 EverMemOS 仍用旧 embedding model，要检查是否已重启。
- 修改 EverMemOS 支持的环境变量名时，这里的硬编码字符串要同步更新；两处没有共享常量，容易遗漏。
