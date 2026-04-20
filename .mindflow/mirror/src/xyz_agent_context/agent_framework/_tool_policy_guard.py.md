---
code_file: src/xyz_agent_context/agent_framework/_tool_policy_guard.py
last_verified: 2026-04-20
stub: false
---

# _tool_policy_guard.py — Claude Code CLI PreToolUse sandbox hook

## 为什么存在

Claude Code CLI 的 `permission_mode="bypassPermissions"` 让 Agent 可以无阻塞地调用任何工具——这对多轮自动化执行是必要的，但也意味着 CLI 本身不会阻止 Agent 读用户隐私文件、全局安装二进制、在不支持 server-tool 的 provider 上调 `WebSearch` 空等 45 秒。这个文件在 PreToolUse 阶段装一个 hook，在那一刻根据「部署模式 + provider 能力」做最后一道拦截。Hook 在 permission-mode 检查之前触发，所以 bypass 模式下依然生效。

## 上下游关系

- **被谁装**：`agent_framework/xyz_claude_agent_sdk.py` 在构造 `ClaudeAgentOptions` 时通过 `build_tool_policy_guard(workspace=..., supports_server_tools=..., mode=...)` 创建 hook，注册到 `HookMatcher(matcher="Read|Glob|Grep|WebSearch|Bash", hooks=[policy_guard])`
- **依赖谁**：`utils/deployment_mode.get_deployment_mode()` 决定云/本地；`api_config.claude_config.supports_anthropic_server_tools` 决定 WebSearch 是否可用
- **和谁必须同步改**：`module/skill_module/skill_module.py` 的 `WORKSPACE_RULES_CLOUD/LOCAL`——Agent 看到的 prompt 规则必须和这里的硬约束一致，否则会出现「prompt 允许但 hook 拒绝」或反之的混乱

## 四条策略

1. **Workspace 越界拦截（仅云端）**：`Read` / `Glob` / `Grep` 的目标路径必须落在 per-agent 工作空间内，`Path.resolve()` 后用 `relative_to(workspace)` 验证，走软链接到外部也拦。本地模式跳过——是用户自己的机器。
2. **Server-tool 拦截（两种模式都开）**：`WebSearch` 依赖 Anthropic 的 `web_search_20250305` server tool，聚合商（NetMind/OpenRouter/Yunwu）不实现，调用会静默 hang 45 秒再超时。`supports_server_tools=False` 时直接拒，prompt 里提示 Agent 转用 `WebFetch`。
3. **`lark-cli` shell-out 重定向（两种模式都开）**：直接 `lark-cli` 或 `npm install @larksuite/cli` 等方式绕过 MCP 层会跳过凭证注入和工作空间隔离。拦截并提示 Agent 走 `mcp__lark_module__*` 工具。
4. **全局安装拦截（仅云端）**：`brew install`、`npm install -g`、`yarn global add`、`apt-get install`、`sudo ...`、裸 `pip install`（不带 `--target=`/`--user`）全部拒绝——这些会改到共享宿主机影响其他租户。回复提示里告诉 Agent 改用 `pip install --target=./libs`、workspace-local `npm install`，或者 `send_message_to_user_directly` 告知用户该 skill 在云端不支持。

## 设计决策

**`mode` 参数允许测试注入**：默认 `mode=None` 走 `get_deployment_mode()` 读环境变量，但测试和未来的显式调用可以直接传 `mode="cloud"`/`"local"`。避免 test 文件需要 monkeypatch 环境变量才能构造不同行为。

**云端缺省更严格**：`_resolve_workspace_rules` 和 hook 本身在 `mode=None` 时都应该偏向云端语义。这里由 `get_deployment_mode()` 兜底——env 不设置会落到 `local`（desktop 默认值安全），但如果 `DATABASE_URL` 指向非 sqlite 则认为是云端（legacy heuristic，保留老云端部署不必改 .env 即可工作）。

**CLI 级的 `disallowed_tools` 是 defense-in-depth**：hook 不会传播到 Task spawn 出去的 subagent 子进程（subprocess-level 隔离），所以调用方在 `supports_server_tools=False` 时额外把 `WebSearch` 加到 `ClaudeAgentOptions.disallowed_tools`，这个是进程级生效。

## Gotcha / 边界情况

- **`Grep` / `Glob` 无 path 参数时缺省是 CWD**：CWD 就是 workspace 根，所以放行。但如果上层改了 CWD（几乎不会），这里的假设就破了。
- **`--target=` 有空格也要认**：`_PIP_INSTALL_SCOPED_FLAGS = re.compile(r"(--target[=\s]|--user\b)")` 同时匹配 `--target=./libs` 和 `--target ./libs`。
- **云端全局拦截的提示文本不是 prompt 约束，是运行时拦截**：Agent 看到的 `WORKSPACE_RULES_CLOUD`（在 prompts 层）和这里 `_deny()` 的 reason 文本应该说法一致，避免 Agent 一边看规则说「可以 pip --user」，一边被运行时拒掉（表达不一致很打击 Agent 的置信度）。
- **legacy 别名 `build_workspace_read_guard`**：旧的 `_workspace_read_guard.py` 重命名过来的，别名保留是因为可能还有地方通过旧名字 import。别名默认 `supports_server_tools=False` + `mode=None`（即由 env 决定），符合最小惊喜。

## 新人易踩的坑

- HookMatcher 的 `matcher` 是正则字符串，不是 glob。如果要新增 gated tool，记得同步更新 `xyz_claude_agent_sdk.py` 里的 `"Read|Glob|Grep|WebSearch|Bash"`。
- Pattern 别只匹配行首——`_GLOBAL_INSTALL_PATTERNS` 每条都带 `(?:^|[\s;&|` "`" `$(])` 前缀，允许 `cd foo && brew install bar`、`do_stuff; sudo x` 这种真实世界的 shell 片段也被抓到。
- 新增 server tool（比如未来的 `computer_use`）需要在 `_SERVER_TOOLS` 里加进去，否则 provider 不支持时会 hang。
