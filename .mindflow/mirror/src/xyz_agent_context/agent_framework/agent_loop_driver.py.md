---
code_file: src/xyz_agent_context/agent_framework/agent_loop_driver.py
last_verified: 2026-07-15
stub: false
---

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-06-17 — Executor seam:per-user `executor_url` 优先,`AGENT_EXECUTOR_URL` 兜底

`get_agent_loop_driver` 新增 keyword-only 参数 `executor_url`:非空时返回
`RemoteAgentLoopDriver` 打到**该用户**的 Executor 容器(由 broker 现取,见
`broker_client.py` + step_3)。未传则回退到静态 env `AGENT_EXECUTOR_URL`;
都没有(本地/桌面,或 executor 容器自身)→ 注册表里的本地 driver,行为不变
(铁律 #7)。优先级:`executor_url` 参数 > `AGENT_EXECUTOR_URL` env > 本地。
这是把 step-3 的 claude/codex spawn 收敛进**每用户隔离容器**的接缝
(铁律 #20 控制面/数据面分离 + per-user 工作区挂载隔离)。

## 2026-06-17 — 默认 framework 名 "claude" → "claude_code"

`DEFAULT_AGENT_LOOP_FRAMEWORK` 从 `"claude"` 改名为 `"claude_code"`，文档串里的
fallback 说明同步更新。意图是把默认 driver 的名字对齐到实际注册的
claude-code agent-loop driver（与新引入的 `codex_oauth` 等 provider 形成清晰的
命名空间），避免「默认值写的名字根本没人注册」导致 `get_agent_loop_driver`
当场 ValueError。纯重命名，注册/选择优先级机制不变。

# agent_loop_driver.py — 可插拔 Agent 框架的注册表（铁律 #9 的落地点）

## 为什么存在

step_3 过去直接 `ClaudeAgentSDK(...).agent_loop(...)`，把整个平台焊死在一个
agent 框架上——这正是铁律 #9 警告的"一个开关就崩"。本模块把它变成
「名字 → 工厂」的注册表：以后接入第二个框架（完整的 OpenAI Agents loop、
LangGraph、自研 loop）只需 `register_agent_loop_driver("name", Factory)`，
绝不再改 step_3。

## 两条正交的轴，别混

- **provider 轴**（`provider_driver/`）：用谁的 endpoint / key。
- **framework 轴**（本模块）：用哪套 agent-loop 协议。

两者组合：framework driver 仍通过 provider 层解析 model/endpoint。换模型供应商
动 provider_driver；换 agent SDK 动这里。

## 选择优先级（越具体越优先）

1. `get_agent_loop_driver(framework=...)` 显式传入——per-agent 扩展点。
2. 环境变量 `AGENT_LOOP_FRAMEWORK`。
3. `DEFAULT_AGENT_LOOP_FRAMEWORK` = "claude"。

## 坑

- `ClaudeAgentSDK` 在 `agent_framework` 包导入时（`__init__.py`）自注册为
  "claude"，类本身即工厂（`__init__(working_path=...)` 已符合工厂契约）。
  **导入包这件事才会填充注册表**——定义了但没被导入的 driver 找不到。
- 未知 framework 名 → `get_agent_loop_driver` 抛 ValueError，**不会**静默回退到
  claude。配置写错要当场炸，而不是伪装成默认值。
- `AgentLoopDriver` Protocol 的签名精确镜像 `ClaudeAgentSDK.agent_loop`；那个方法
  就是每个新适配器必须对齐的参考形状（yield 原始事件 dict 给 ResponseProcessor）。
