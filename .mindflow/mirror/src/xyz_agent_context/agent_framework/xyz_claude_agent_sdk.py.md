---
code_file: src/xyz_agent_context/agent_framework/xyz_claude_agent_sdk.py
last_verified: 2026-04-20
stub: false
---
# xyz_claude_agent_sdk.py — Claude Code CLI 主 Agent Loop 适配层

## 为什么存在

Claude Code CLI 是一个独立的命令行工具，通过 `claude_agent_sdk` Python SDK 以子进程方式驱动。这个文件把 SDK 的低级接口（connect/query/receive_response）封装为系统期望的 `async generator` 接口，并处理：多轮对话历史拼接到 system prompt（CLI 不原生支持多轮）、流式消息格式转换（通过 `output_transfer.py`）、`tool_call_id` 去重（`include_partial_messages=True` 导致的重复事件）、取消信号传播、空消息检测、idle timeout。

## 上下游关系

被 `step_3_agent_loop.py` 调用，在 Step 3.4 中启动 agent loop，接收所有流式事件并 yield 给上层。上层拿到的事件由 `response_processor.py` 解析为类型化消息。

配置通过 `api_config.claude_config`（ContextVar proxy）获取，确保每个 asyncio task 使用 owner 的配置。MCP 服务器 URL 由调用方传入（`mcp_server_urls`），包含所有激活 Module 的 MCP 端点。

`output_transfer.py` 是直接依赖，把每条 Claude SDK 消息转换为事件列表后才 yield。

## 设计决策

**多轮对话拼接到 system prompt**：Claude Code CLI 的 `ClaudeAgentOptions` 不支持 messages 数组，只有 `system_prompt` 和单条 `query`。所以所有历史对话都被格式化为文本追加到 system prompt 末尾，超出 60KB 时截断保留最近部分。这是已知限制，等 SDK 支持 multi-turn 后可以去掉。

**`_safe_parse_message` monkey-patch**：SDK v0.1.6 遇到未知消息类型（如 `rate_limit_event`）会抛 `MessageParseError` 崩溃整个 loop。patch 把它转为 `SystemMessage` 继续运行。这是针对 SDK 版本 bug 的防御性措施，升级 SDK 后要验证是否还需要。

**`NO_PROXY` 和 `CLAUDECODE` 环境变量注入**：系统代理可能导致 Claude CLI 子进程访问 localhost MCP 服务器走代理返回 502。`CLAUDECODE=""` 是为了防止嵌套 Claude Code 会话检测阻止子进程启动（当后端在 Claude Code 终端内运行时）。

**`max_buffer_size=50MB`**：MCP 工具（如 PDF 解析）可能返回大量内容，默认 buffer 太小会导致响应被截断。

**1200 秒 idle timeout**：用 `asyncio.wait_for` 包装每次 `__anext__()`，如果 CLI 超过 20 分钟无响应则中止。这防止僵尸 agent 进程永久占用资源。

**`build_tool_policy_guard` 注入 PreToolUse hook 做沙箱**：CLI 本身没有工作空间隔离概念，也不知道 WebSearch 需要 Anthropic 服务端工具。我们在这里装一个 hook（`_tool_policy_guard.py`），在云端部署下强制 Read/Glob/Grep 只能访问 workspace、Bash 不允许全局安装（brew/npm -g/apt/sudo/裸 pip），在任何模式下把 `lark-cli` shell-out 重定向到 MCP、把无 server-tool 的 provider 调 WebSearch 拦下来改用 WebFetch。hook 在 `permission_mode="bypassPermissions"` 之前触发，所以即使 bypass 也生效。`HookMatcher` 的 `matcher` 必须覆盖 `Read|Glob|Grep|WebSearch|Bash`。

## Gotcha / 边界情况

- `include_partial_messages=True` 导致 partial 和 complete `AssistantMessage` 都携带 `ToolUseBlock`，同一 `tool_call_id` 会出现两次。去重通过 `seen_tool_call_ids` set 在这里处理，`output_transfer.py` 不处理去重。
- 0 条消息收到时 log error 但不抛出异常——调用方会收到一个空 `final_output` 的 `PathExecutionResult`。这是静默降级，可能让用户看到空回复而不是错误提示。
- `client.disconnect()` 在 cancel scope 错误时被静默忽略（anyio cancel scope 兼容性问题），正常 RuntimeError 仍会抛出。

## 新人易踩的坑

- `this_turn_user_message = (messages.pop())["content"]`：这里假设最后一条消息是 user message。如果调用方构建 messages 时最后一条不是 user message，会产生逻辑错误。代码注释里也标注了这个 TODO。
- 直接在本地测试时，`claude` CLI 必须已经登录（`claude auth login`），否则会收到 0 条消息且没有明显错误——只有 stderr log 里有认证失败信息。
