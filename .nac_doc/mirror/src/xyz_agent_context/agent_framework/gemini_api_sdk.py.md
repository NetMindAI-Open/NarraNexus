---
code_file: src/xyz_agent_context/agent_framework/gemini_api_sdk.py
last_verified: 2026-04-10
stub: false
---
# gemini_api_sdk.py — Gemini LLM 适配层（PDF 原生多模态专用）

## 为什么存在

项目主 agent 跑在 Claude Agent SDK，helper LLM 跑在 OpenAI-compatible 端点，但 Gemini 的原生文件上传+多模态能力（尤其大文件 PDF 解析）需要 Google 原生 `genai` SDK，无法通过 OpenAI-compatible 接口访问。这个文件把 `google-genai` 包装成与 `openai_agents_sdk.py` 相似的 `llm_function` 接口，让上层调用者可以以一致方式使用，同时集成 cost tracking。

## 上下游关系

唯一直接消费者是 `gemini_rag_module`（`GeminiRAGModule`），通过 `llm_function(instructions, user_input, output_type, file_path)` 调用。`file_path` 非空时走 PDF 上传路径；为 `None` 时走纯文本路径。

配置来自 `api_config.gemini_config`，但 Gemini 尚未纳入三 slot 系统（`gemini_config` 不走 ContextVar，只来自全局 settings），意味着多租户场景下所有用户共用同一个 Gemini API key。

cost tracking 通过 `cost_tracker.record_cost()` 异步记录。如果调用时没有传 `agent_id`/`db` 参数，则从全局 `get_cost_context()` 获取（由 `agent_runtime.py` 在 run 入口注入）。

## 设计决策

**和其他两个 SDK 的分工**：`openai_agents_sdk.py` 处理所有 OpenAI-compatible 的 helper LLM 调用（包括代理和本地模型），`xyz_claude_agent_sdk.py` 驱动主 agent loop，`gemini_api_sdk.py` 是专用工具，只做 Google 特有的事情（文件上传 + 生成）。三者之间没有继承关系，接口相似但独立实现。

**`file_path` 目前限制为 PDF**：`_make_response_with_pdf` 方法之外有 `raise ValueError`，只支持 `.pdf` 扩展名。后续支持其他格式需要在此处扩展。

**同步 API 包装为 async 方法**：`genai.Client` 的 `models.generate_content` 和 `files.upload` 是同步调用，但方法签名是 `async def`。高并发下会阻塞 asyncio event loop，这是已知的技术债。

## Gotcha / 边界情况

- `_make_response_with_pdf` 内部 `client.files.upload` 是同步操作，可能耗时数秒，在 asyncio 环境中会阻塞整个 event loop。
- 函数返回的是 Gemini response 对象，不是字符串。调用方需要从 `response.text` 等属性提取内容。这和 `openai_agents_sdk.py` 的 `result.final_output` 接口不兼容，不能混用。
- Gemini 计费用 `usage_metadata.candidates_token_count`（不是常见的 `completion_tokens`），`_record_usage` 里有对应映射。

## 新人易踩的坑

- 误以为 Gemini 也走 slot 系统和 ContextVar 隔离。它目前不走，多租户部署时所有 agent 共用 `settings.google_api_key`，计费归同一账号。
- 单元测试里直接 mock `gemini_config.api_key` 不生效，因为 `GeminiAPISDK.__init__` 里读的是 proxy 的属性，测试需要 patch `genai.Client` 本身或整个 `api_config.gemini_config` 对象。
