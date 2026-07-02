---
code_file: src/xyz_agent_context/agent_framework/__init__.py
last_verified: 2026-06-17
stub: false
---
# agent_framework/__init__.py — agent-loop driver 注册中心

## 为什么存在

这个 `__init__` 是 framework 轴（"用哪套 agent-loop 协议"）的注册门面。它在 import
时把各 SDK driver 按**名字**注册进 `agent_loop_driver` 的全局表里；下游
`step_3_agent_loop` 只读 `user_slots.agent_framework` 这个字符串，再用
`get_agent_loop_driver(name)` 查表拿到 driver——**没有任何下游硬编码具体类名**。
这正是铁律 #9（不绑死任何一个 Agent 框架）的落地点：换框架 = 在这里加一行
`register_agent_loop_driver`，上层不动。

注意区分这里的两条抽象轴：framework 轴（本文件 + `agent_loop_driver`）决定"哪套
loop 协议"；provider 轴（`provider_driver/`）决定"哪个 endpoint / key"。两者正交。

## 2026-06-17 — 收敛成「一个框架一个 canonical 名」+ codex_cli 切官方 SDK

PR #25 把注册表清理成**每个框架只留一个规范名**：

- `"claude_code"` → `ClaudeAgentSDK`（原来注册名是 `"claude"`）
- `"codex_cli"` → `CodexSDKv2`（官方 `openai-codex` SDK 的 driver）

清理掉的东西：

- **A/B 别名 `codex_cli_v2` / `codex_official` 删除**。这两个是 v2 灰度切换期为了
  平滑过渡临时注册的；现在 v2 是唯一注册的 codex driver，别名就是噪音。**遗留风险**：
  A/B 期落库的 DB 行若还带这两个名字，resolution 会抛 `ValueError`——靠用户在
  Settings 里重新选一次 "Codex CLI" 修正（无向后兼容垫片，铁律 #2）。
- **legacy shorthand `"claude"` / `"codex"` 删除**：本意给 env / CLI override 用，
  实际零调用方，纯 clutter。一律用 canonical 名。

设计决策 / gotcha：

- **v1 `CodexSDK` 保留可 import 但不注册**（复活回退）：手写的 v1 driver 还在
  `xyz_codex_cli_sdk.py`，文件故意留着。若官方 SDK 出严重 regression，在本文件加
  `register_agent_loop_driver("codex_cli", CodexSDK)` 即可一键切回。
- **`CodexSDKv2` 的 import 用 try/except 包住**：slim 部署可能不装 `openai-codex`，
  此时 `CodexSDKv2 = None` 且 `codex_cli` 保持未注册，调用方会从
  `get_agent_loop_driver` 拿到干净的 `ValueError`，而不是 import 期整个包加载失败。
  失败路径打 warning 而非静默——告诉运维去装依赖或复活 v1。
- `codex_cli` 框架还携带 per-call 的 `codex_config` ContextVar（auth + model 形状见
  `api_config.CodexConfig`），由 resolver 在每轮 turn 前填好。这里只负责把
  `CodexConfig` / `codex_config` 一并 re-export 供下游引用。
