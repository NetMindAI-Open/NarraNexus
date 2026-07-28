---
code_dir: src/xyz_agent_context/agent_framework/
last_verified: 2026-07-24
stub: false
---
# agent_framework/ — LLM 适配层与 provider 管理

## 目录角色

这个目录是 NarraNexus 与各 LLM provider / Agent 框架之间的隔离层，实现铁律 #9
「不强依赖某一个 Agent 框架或 LLM」。它对上层（`agent_runtime/`、`narrative/`、
各 module）暴露统一接口，让换 provider / 换框架不需要改动业务逻辑。

2026-07-24 起按「一次 agent run 生命周期中的角色」分为四组（此前 34 个文件平铺）。

## 分组索引

### `loop/` — Agent-loop 执行层
驱动一次完整 agent run：`driver.py`（可插拔 loop 框架抽象 + 注册表）、
`remote_driver.py`（把 loop 委托给 Executor 服务）、`broker_client.py` +
`executor_errors.py`（per-user Executor Broker 客户端与传输层异常）、
`output_transfer.py`（各 SDK 输出 → 统一事件字典的无状态转换层）、
`circuit_breaker.py`（实时对话层熔断）。

### `adapters/` — Agent 框架适配器（铁律 #9 的 swap seam）
`claude/`（`sdk.py` 驱动 Claude Code CLI 子进程、处理流式输出；`prompts.py`
它专属的 prompt 常量——chat history 格式、截断警告）、`codex/`（`cli_sdk.py`
旧 CLI 包装、`official_sdk.py` 官方 SDK 包装、`_env` / `_config_toml_builder` /
`_permission_translator` 三个私有 helper）、`openai_agents.py`（OpenAI 兼容
function caller，支持结构化输出 + think-block fallback）、
`_tool_policy_guard.py`（跨适配器共享的 PreToolUse 策略钩子）。

### `llm/` — LLM 原子操作（单发调用，无 loop）
`helper_sdk.py`（协议键控的 helper_llm 工厂）与 `anthropic_helper` /
`cli_helper` / `gemini_api`（Gemini 原生 SDK，PDF 上传与多模态推理）三个后端、
`failure.py`（provider 错误分类与脱敏）、`transcription/`（音频→文本子系统）。

### `providers/` — 供应商与模型目录体系
`registry.py`（本地单机 `~/.nexusagent/llm_config.json` 管理，5 种 provider
card）、`resolver.py`（per-request 用户/系统配置路由）、`readiness.py`
（用户可运行性检查）、`system_service.py` / `user_service.py`（系统默认 env /
云端多租户 per-user provider+slot，存数据库）/ `slot_service.py`（per-agent
slot 覆盖）、`model_catalog.py`（静态模型元数据与默认模型列表）、
`model_sync.py` + `model_probe_ledger.py`（聚合商模型自动发现与探测台账，
JSON 随包）、`cloud_policy.py`（云端 netmind-only slot 策略）、
`model_identity.py`（运行时真实模型身份解析）、`driver/`（provider driver
子系统：base / derive / registry / resolver / self_heal / backfill + drivers/）。

### 根部横切面
`api_config.py`（所有 LLM 配置的唯一入口，ContextVar per-task 隔离与
`set_user_config()` 多租户机制）、`quota_service.py`（免费额度执行门，被
内核 / worker / backend 三方消费）、`__init__.py`（驱动注册接线点 + 稳定的
包级公共符号面——内部重组不影响
`from xyz_agent_context.agent_framework import X` 的消费者，且自述模块地图）。

## 和外部目录的协作

- `agent_runtime/` 在 `run()` 入口调用 `get_agent_owner_llm_configs()` 和
  `set_user_config()`，经 `loop/driver` 的注册表选择框架
- `narrative/` 包通过 `llm/`（helper 决策）间接使用这里的配置
- `backend/routes/providers.py` 等直接使用 `providers/` 的 `registry` 与
  `user_service`
- `services/model_sync_runner`（compose 入口，不在本目录）内部调
  `providers/model_sync`
- `schema/provider_schema.py` 定义 `LLMConfig` / `ProviderConfig` /
  `SlotConfig` 等数据模型，`agent_framework/` 大量使用但不定义这些 schema
