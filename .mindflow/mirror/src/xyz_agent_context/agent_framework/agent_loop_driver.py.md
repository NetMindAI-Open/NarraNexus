---
code_file: src/xyz_agent_context/agent_framework/agent_loop_driver.py
last_verified: 2026-05-29
stub: false
---

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
