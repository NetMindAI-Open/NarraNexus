---
code_file: src/narranexus/platform/module_system/__init__.py
last_verified: 2026-09-07
---

## 2026-09-07 — caller identity 的**服务端**解析器也转公开（批 6c，A2-1）

原来的注释写着「注入侧公开、服务端解析保持私有」。批 6b 之后这条界线站不住了：
每个 MCP 工具都住进了独立的插件 wheel，而调用 `caller_user_id_from_request` /
`caller_team_id_from_request` / `caller_event_id_from_request` / `caller_root_run_id` /
`caller_turn_source` / `caller_errand_scope` / `resolve_caller_agent_id` 的**正是**它们
——实测 7 个插件调用点在直接 import `module_system._mcp_identity`。
「包内私有」对一个另行打包的消费者不成立，于是这 7 个符号从门面公开。
`_mcp_identity` 里的其余部分（bearer 解析细节、placeholder 表）仍然私有。

## 2026-09-07 — header redrawn

The file header now draws the post-batch-6 tree (`module_system/` with `registry.py` / `contributions.py`; concrete modules in `plugins/builtin.<id>/src/narranexus_plugins/<pkg>/`). Comment-only change.

## 2026-09-04（批 3c.1）— 门面不再 import 任何内置模块

17 行 eager import 删除；`module_registry = ModuleRegistry(...)`；`from narranexus.platform.module_system import ChatModule`
经 PEP 562 `__getattr__` 惰性从视图取（被禁用/导入失败 → AttributeError）。这是 spec §20 批 3 出口判据
「platform 不再 import 任何 builtin」在模块层的落地。

## 2026-09-03（批 2f.1）— `module_registry["NexusPluginsModule"]`
## 2026-08-10 — 导出 IDENTITY_TOKEN_HEADER / stamp_identity_token / BEARER_AGENT_PREFIX / parse_bearer_identity

MCP caller auth(蓝图 P1):包外消费方是 [[step_3_agent_loop.py]](dispatch 时
stamp broker/本地签的身份 token)与 backend/auth、identity/verify(bearer 记录
是跨进程契约,解析走公开名 `parse_bearer_identity`,review #4),沿用
「注入面走公开面」的规矩。

## 2026-08-07 — 导出 `TEAM_ID_HEADER` / `EVENT_ID_HEADER`

随 [[_mcp_identity.py]] 新增的两个身份 header 常量一并导出。

## 2026-08-07 — 导出 ROOT_RUN_ID_HEADER

注入面继续走公开面(2026-08-04 定的规矩:私有模块不跨包被 import),
服务端解析仍留在 `_mcp_identity`。

## 2026-08-04 — 导出调用者身份的注入面

`AGENT_ID_HEADER` / `TURN_SOURCE_HEADER` / `USER_ID_HEADER` / `ERRAND_PEER_HEADER` /
`ERRAND_CHANNEL_HEADER`(后两个 2026-08-03 加,承载本轮差事作用域)/
`agent_id_headers` 经本文件导出:
[[context_runtime]] 在包外组装 per-agent mcp spec,原先直接 import
`._mcp_identity`(私有模块被当公共 seam 用)。服务端解析仍留私有。
包 docstring 的目录树同批补上 `_mcp_identity.py`。

> 2026-06-22：`NarramessengerModule` 加入 module_registry / `__all__`（capability
> module，NarraMessenger gateway-poll IM channel）。
>
> 2026-05-29：`MemoryModule` 从 module_registry / CAPABILITY_MODULES / __all__
> 移除（EverMemOS 整体删除，memory_module 包已删）。
>
> 2026-05-29：新增 `module_class_provides_chat_history(name)` 助手——按
> module_class 字符串经 module_registry 查 `provides_chat_history()` 能力标志，
> 让编排层不必硬编码 `== "ChatModule"`（见 [[base.py]] 能力标志契约）。

# __init__.py — module_registry 注册表与包导出

## 为什么存在

这个文件是整个模块系统的"目录"。它做三件事：定义 `module_registry`（字符串名 → 类的映射）、触发 `rebuild_module_instance_model()`（解决 Pydantic forward reference），以及聚合包的公开 API 供外部 import。

## 上下游关系

- **被谁用**：`ModuleService.__init__` 通过 `from narranexus.platform.module_system import module_registry` 获取注册表；任何需要 `ModuleService`、`HookManager`、`XYZBaseModule` 的外部代码都从这里导入
- **依赖谁**：所有具体 Module 类（循环地）；`_module_impl/` 的工具类；`schema/module_schema.py` 的 `rebuild_module_instance_model`

## 设计决策

**`module_registry` 是注册的唯一入口**：新模块必须在这里注册，否则 `ModuleLoader` 永远不会加载它。这是故意的集中化——避免自动发现（annotation scanning）带来的不透明性。

**`rebuild_module_instance_model()` 在 import 时调用**：`ModuleInstance` schema 里有 `Optional["XYZBaseModule"]` forward reference，在所有 Module 类都定义完之后才能 resolve。这个调用必须在 `__init__.py` 里执行，因为这是所有类都已 import 之后最早的时机。

**`MemoryModule` 排在 `module_registry` 第一位**：注释说"最高优先级，确保在其他模块之前执行"。这依赖 `ModuleLoader` 在顺序执行 `gather` 时保留 `module_registry` 的顺序，`MemoryModule` 需要先把 EverMemOS 查询结果缓存到 `ctx_data.extra_data`，后续的 `ChatModule` 才能读取。

## Gotcha / 边界情况

- 在顶部添加任何会触发循环导入的语句（比如导入 `module_service`）会让整个包 import 失败，症状是难以理解的 `ImportError`。
- 新增 Module 类但只 import 不加到 `module_registry`，该模块永远不可用，且不会有任何报错。

## 新人易踩的坑

- 注册了新 Module 但忘记在 `DEFAULT_MCP_MODULES`（`module_runner.py`）和对应的 `MODULE_PORTS` 里添加端口配置，导致 MCP 服务器启动时端口冲突或无法访问。

## 2026-09-04 · exports the MCP address helpers (batch 5a)

`mcp_port`, `mcp_base_url`, `mcp_mount_path`, `mcp_server_url` re-exported next to `mcp_host` for the modules that import from the package.

## 2026-09-04 · registry-backed module declarations (batch 5b)

`module_config(name)`, `module_configs()`, `is_task_module(name)`, `module_by_role(role)` ("chat" / "awareness" / "social_network" / "jobs") and `instance_prefix_for(name)` — the one lookup behind every former constant table. Orchestration code asks the registry instead of naming `"JobModule"` / `"ChatModule"`.

## 2026-09-04 · `module_registry` (batch 5d)

`MODULE_MAP`, `_MODULE_CLASS_NAMES` and the lazy `__getattr__` class re-export are gone; `module_registry` / `ModuleRegistry` are the registry view and the helpers (`module_config`, `module_by_role`, …) read it. Import a module class from its own package.

## 2026-09-04 · no module packages under module_system (batch 6b)

The 17 builtin module packages moved to `plugins/builtin.*` (`narranexus_plugins.<pkg>`); what remains here is the module SYSTEM: base class, registry, loader, hooks, seam, runner, capability service.

## 2026-09-07 — no import-time registration

The builtin module table is no longer registered when the package imports; the host boot registers the manifests' contributions and module_registry is the live view.
