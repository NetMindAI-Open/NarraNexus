---
code_file: src/narranexus/platform/agent_framework/__init__.py
last_verified: 2026-09-07
stub: false
---
# agent_framework/__init__.py — agent-loop driver 注册中心

## 2026-09-03（批 1）— 三个 Contribution 携带 `FrameworkMeta`（含安装配方）

`NEXUS_POWER/CLAUDE_CODE/CODEX_CLI` 的 `meta["framework"]` 是 `FrameworkMeta`；claude_code/codex_cli
的 `FrameworkInstall` 把原来 `backend/integrations/plugins/registry.py` 里的 pip/npm 钉版本、探测包、
体积提示搬到这里（npm 版本仍引用 `adapters.claude.cli_binary.PINNED_CLI_VERSION`）。backend 的
`build_plugin_specs()` 按需从注册表派生（不再有 import 期快照），锁步测试 `test_pip_pins_match_uv_lock` 继续守 uv.lock。

## 2026-09-03 — 三框架改为 `Contribution` 常量（`NEXUS_POWER/CLAUDE_CODE/CODEX_CLI`）

插件平台批 0：三个惰性工厂不再经 `register_agent_loop_driver` 注册，而是各自包成
`narranexus.kernel.plugins.registry.Contribution`（名字 + `lambda: 工厂` + `meta["framework"]` 的 `FrameworkMeta`，display_name 在其中），
以各自的插件 id（`builtin.frameworks.<name>`）作 owner 注册进 `FRAMEWORK_REGISTRY`。
`narranexus.kernel.plugins.builtins` 的 manifest 按符号名引用这三个常量，loader 注册同一对象是
幂等 no-op。惰性/`activate_pyenv`/PEP 562 逻辑全部不变。

## 2026-08-28 — 轻量化插件：三 driver 全惰性工厂 + 惰性公共名

本地版把 `claude-agent-sdk`（Claude）/`openai-codex`（Codex）改成**按需安装的
插件**，故 import 本包**绝不能**要求任一 SDK 在场。改动：

- 去掉 import 期 `from .adapters.claude.sdk import ClaudeAgentSDK`；三个框架
  （nexus_power/claude_code/codex_cli）都注册**惰性工厂**，SDK import 只在工厂
  真正建 driver 时发生。codex 从旧的 try/except-import-time 也改成惰性工厂
  （否则 base 移除 openai-codex 后它就不注册了）。
- 每个工厂先 `plugin_paths.activate_pyenv()`（把插件 pyenv append 进 sys.path），
  故插件在 app 运行中装完**免重启**即可用；包 import 末尾再调一次覆盖启动时已装。
- 公共名 `ClaudeAgentSDK/CodexSDK/CodexSDKv2` 经 PEP 562 `__getattr__` 惰性暴露，
  仍可 `from ...agent_framework import ClaudeAgentSDK`，但只在真取用时才拉 SDK。
- **注册 ≠ 已装**：可用性判定在 [[plugin_paths]] 的 `framework_installed`；
  fail-closed 在 [[driver]] 的 `get_agent_loop_driver`。

## 2026-07-29 — 注册第三个 driver:nexus_power

`register_agent_loop_driver("nexus_power", _nexus_power_factory)`——工厂内懒 import
[[nexus_agent]],注册在包导入期零成本(不像 claude/codex 直接 import 适配器)。
声明与实现同批(capabilities={event_log} 随实现落地)。

## 为什么存在

这个 `__init__` 是 framework 轴（"用哪套 agent-loop 协议"）的注册门面。它在 import
时把各 SDK driver 按**名字**注册进 `loop.driver` 的全局表里；下游
`step_3_agent_loop` 只读 `user_slots.agent_framework` 这个字符串，再用
`get_agent_loop_driver(name)` 查表拿到 driver——**没有任何下游硬编码具体类名**。
这正是铁律 #9（不绑死任何一个 Agent 框架）的落地点：换框架 = 在这里加一行
`register_agent_loop_driver`，上层不动。

注意区分这里的两条抽象轴：framework 轴（本文件 + `loop.driver`）决定"哪套
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
  `adapters/codex/cli_sdk.py`，文件故意留着。若官方 SDK 出严重 regression，在本文件加
  `register_agent_loop_driver("codex_cli", CodexSDK)` 即可一键切回。
- **`CodexSDKv2` 的 import 用 try/except 包住**：slim 部署可能不装 `openai-codex`，
  此时 `CodexSDKv2 = None` 且 `codex_cli` 保持未注册，调用方会从
  `get_agent_loop_driver` 拿到干净的 `ValueError`，而不是 import 期整个包加载失败。
  失败路径打 warning 而非静默——告诉运维去装依赖或复活 v1。
- `codex_cli` 框架还携带 per-call 的 `codex_config` ContextVar（auth + model 形状见
  `api_config.CodexConfig`），由 resolver 在每轮 turn 前填好。这里只负责把
  `CodexConfig` / `codex_config` 一并 re-export 供下游引用。

The three builtin frameworks no longer live here (batch 6b.2b): their factories, `Contribution`s and SDK re-exports moved to the `builtin.frameworks.*` plugin packages; this package only activates the plugin pyenv and re-exports the loop-driver API.

## 2026-09-07 — docstring 更新（B6）

framework_installed 指向 loop.driver。
