---
code_file: src/narranexus/platform/module_system/base.py
last_verified: 2026-09-07
---

## 2026-09-07（批 1 三轮复审移植）— `meta` 不再夹带 `enabled`

`CapabilityMeta` 是静态描述；`ModuleConfig.enabled` 是每 agent 状态，之前塞进 `meta.requires["enabled"]` 会与
`CapabilitySet.is_enabled` 形成两个真源（`requires` 的语义是运行时依赖）。现在 `requires` 留空，`enabled` 只在
`self.config` 上；`tests/module/test_module_is_a_capability.py` 钉住 `requires == {}`。全仓无消费方读过那个键。

## 2026-08-04 (review 修正) — working_source_matches 公共谓词

四处 claims_source 覆写各写一套 enum/字符串比较（两种 isinstance
极性相反、第二子句全是死代码——WorkingSource 是 (str, Enum)，一个 ==
两形态通吃）。抽 `working_source_matches(ws, name)` 模块级函数，
chat/bus/channel 基类共用。

## 2026-08-04 — 新增 claims_source 钩子（origin-first 排序的数据源）

`claims_source(working_source) -> bool` 默认 False。声明「本模块是
该来源轮次的主人」；[[context_runtime]] 的 expressive 收集用它把来源模块
的声明排最前，使默认回复工具跟随本轮联系渠道（原则：独白=思考、交付走
工具、工具跟来源走，不写死 owner-chat 默认）。覆写者：chat（CHAT）、
message_bus（MESSAGE_BUS）、channel 基类（channel_name 匹配）。

## 2026-08-03 — `expressive_tools` 增加可选 ctx_data(按来源声明)

回复面声明可按 turn 来源变化——声明面绝不能列出本回合无法投递的死工具
(那是喂给模型的错误信息,弱模型遇声明/指令冲突时常以"写成文字"收场)。
首个消费者:narramessenger 托管回合剔除 trigger 捕获式的 narra_reply,
只声明 narra_send。无 ctx 调用方(测试/旧路径)行为不变。

## 2026-08-01 — build_instrumented_mcp_server:部署面的单一接入点

新增非重写的包装方法:子类继续实现 `create_mcp_server()`(文档化的扩展点),
**服务方改调 `build_instrumented_mcp_server()`**。它在返回前装上平台级接线——
当前是调用者身份解析([[_mcp_identity]])。这样做的收益是 P1 修复
零散落:16 个模块、93 个带 `agent_id` 的工具**一个文件都不用改**,新模块
声明 `agent_id` 就自动获得。永不抛异常:装不上也照样把 server 交出去
(身份解析是增强,不是服务前提)。

命名刻意避开 `ModuleRunner._build_mcp_server`(那个是包 uvicorn app 的,
两回事)。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

模块契约新增 `expressive_tools() -> list[str]`(默认空):模块声明自己
「哪些工具把内容送达人类」(全限定名)。chat/channel 覆写;
[[context_runtime.py]] 与 disallowed_tools 同环收集((priority,
module_class) 全序排、去重、fail-open),经 TurnInput 声明给框架
(NexusPower 独白契约的投递面)。
语义纯通用,零场景词汇(铁律 #4)。

## 2026-07-28 — R4a：新增通用面 `contribute_turn_context(ctx_data) -> str`（默认 ""）

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

模块契约新增"每轮易变内容"的一等公民出口：每轮变化的数据（召回结果、活计数、
时间戳、动态列表）放 `contribute_turn_context`，**`contribute_instructions` 必须轮间字节稳定**
（system prompt 可缓存的前提）。运行时（[[context_runtime.py]]
`_build_turn_context_block`）按 module_class 去重、priority 升序收集非空块进当前
轮 user message 的 `[Turn context]` 块；单模块异常 fail-open（warning + 跳过）。
R4a 只加基类面，R4b 才逐模块 override（BasicInfo/GeneralMemory/SocialNetwork/
Job/MessageBus/CommonTools）。**Code review 检查项：新模块给 contribute_instructions
塞每轮易变字节 = 打穿全网缓存回归**，用 [SYSPROMPT-BREAKDOWN] 的 ctx_sha256
（或 claude 适配器 [SYSPROMPT-SHA] 的 sys_sha256，R4c 起为实发字节权威哈希）
两轮对比定位。语义纯通用，零场景词汇（铁律 #4）。

## 2026-07-24 — generic `disallowed_tools()` surface (setup-residency B++)

New async `disallowed_tools()` on the base, default `[]`: a module may ask
the runtime to strip fully-qualified MCP tools (`mcp__<server>__<tool>`) from
the model context this turn. The generic layer stays scenario-free (rule #4) —
this file only defines the surface; the unbound-channel gating lives in the
channel base override ([[channel_module_base]]). Collected by
[[context_runtime.py]] `build_input_for_framework`.

## 2026-05-29 — capability flags (decouple orchestration from class names)

Added `provides_chat_history()` classmethod (default False). Capability
flags let the pipeline reason about WHAT a module does without hard-coding
WHICH class (`type(m).__name__ == "ChatModule"`). It's a classmethod so it
works on both a live object and a class-name string via module_registry (see
`module.module_class_provides_chat_history`). ChatModule overrides it to
True. New capabilities should follow this same pattern rather than adding
class-name checks in the orchestration layer.

## 2026-05-20 — new `persist_turn` (synchronous, next-turn-critical)

Added a second post-turn hook alongside `after_turn`. The split
exists because the OLD single background hook caused short-reply "amnesia":
ChatModule wrote the conversation row in `after_turn`, which the
runtime dispatches to a background task (can lag 3–19s); a user replying instantly
raced that write and the next turn read history missing the exchange.
`persist_turn` runs SYNCHRONOUSLY in-request (see [[agent_runtime.py]] Step
4.6, [[hook_manager.py]]) for the minimal state the NEXT turn must read (ChatModule
writes the conversation row here — see [[chat_module.py]]); `after_turn`
stays background for heavy/non-critical work (embeddings, entity extraction, LLM
summaries). Default for both is no-op.

# base.py — Module 基类契约

## 为什么存在

`XYZBaseModule` 是整个模块系统的唯一公共契约。它把"一个 Module 必须能做什么"从"某个具体 Module 怎么做"中分离出来。`AgentRuntime` 和 `HookManager` 只依赖这个抽象，从不直接引用具体模块类。

## 上下游关系

- **被谁用**：`ModuleLoader`（`_module_impl/loader.py`）通过 `module_registry` 按名实例化子类；`HookManager` 循环调用 `gather` / `after_turn`；`ModuleRunner` 调用 `create_mcp_server()` 部署 MCP 进程
- **依赖谁**：`DatabaseClient`（`utils/`）同步 wrapper；`AsyncDatabaseClient` 通过 `utils/db/db_factory.get_db_client()` 懒加载（MCP 进程专用）；`schema/` 中的 `ModuleConfig`、`MCPServerConfig`、`ContextData`、`HookAfterExecutionParams`

## 设计决策

**MCP 数据库连接用类变量隔离**：MCP 服务器作为独立进程/线程运行，不能共享主进程连接池。`_mcp_db_client` 是类变量，每个具体子类在自己的运行环境里各持一个连接，第一次调用 `get_mcp_db_client()` 时懒创建。被否决的方案是让 MCP 工具通过内部 HTTP API 向主进程取数据——会引入额外网络跳转且 MCP 工具的低延迟要求不允许。

**`gather` 和 `after_turn` 均有默认空实现**：大多数模块只需要实现其中一个。强制所有子类都实现两个 hook 会造成不必要的样板，且某些仅提供 MCP 工具的模块（如 `BasicInfoModule`）根本不需要 hook。

**`contribute_instructions()` 用 `ContextData` 做动态格式化**：指令字符串里可以有 `{awareness}`、`{jobs_information}` 等占位符，在 `contribute_instructions()` 调用时用当前 `ctx_data` 字段填充。子类只需在 `__init__` 里赋值 `self.instructions`。

**`mcp_server()` 是抽象方法但允许返回 `None`**：这迫使子类明确表态"我有/没有 MCP 服务器"，而不是漏掉这个决定。没有 MCP 服务器的模块（如 `MemoryModule`）直接 `return None`。

## Gotcha / 边界情况

- **`instance_id` vs `instance_ids`**：前者是"当前这个实例的 ID"，后者是"当前 Narrative 里所有同类实例 ID 列表"（用于 `ChatModule` 加载历史时跨实例查询）。两者都可以为 `None`/空。
- **`create_mcp_server()` 默认返回 `None`**：如果子类没有重写这个方法，`ModuleRunner` 会跳过该模块，静默不报错。

## 新人易踩的坑

- 忘记调用 `super().__init__()` 会导致 `self.agent_id`、`self.db` 等属性 `AttributeError`，错误往往在 hook 执行时才暴露，难以追踪。
- 在 MCP 工具里用 `self.db`（同步 wrapper）而非 `await get_mcp_db_client()` 会遇到事件循环不匹配或跨进程连接共享问题，症状是随机 `RuntimeError` 或连接超时。
- 在 `gather` 里修改了 `ctx_data` 字段后没有 `return ctx_data`，修改会被静默丢弃（特别是并行模式下每个模块拿到的是副本）。

## 2026-08-18 — `disallowed_tools` 改为接收 `ctx_data`（修 P0：团队房间说不出话）

原签名不带 ctx。当模块需要「声明一个动词、同时压掉它的对偶」时（[[message_bus_module]]
的 `message_team`/`message_agent`、[[chat_module]] 的 `reply_owner`/`notify_owner`），
它只能从声明 hook 留在实例上的状态里读本轮身份 —— 而 [[context_runtime.py]] 的收集环
**先问压制、后问声明**。于是每个团队轮次在全新实例上拿到空状态，判成非团队轮，把
`message_team` 从桌面撤掉；紧接着声明层又把它宣告为本轮的回复工具。两个框架的回复提醒
都点名一个 schema 已被移除的工具 —— 房间永远收不到消息，且不报错。

修法是消灭顺序依赖而不是记录它：两个 hook 都拿本轮自己的 ctx，谁先被调用都无所谓。
覆写者需同步签名（[[channel_module_base]] / [[chat_module]] / [[message_bus_module]]）——
旧签名会在调用点 TypeError，而该处 **fail-open**，结果是压制静默失效、两把动词都留在桌上。
`tests/context_runtime/test_expressive_collection.py` 新增两条守卫：桌面自洽性（跑真实模块、
按生产顺序、三种轮次断言声明∩压制为空）与 module_registry 全量签名检查。

教训：此前所有守卫都直接调两个 hook 且**反着生产顺序**调，因此在坏代码上全绿。
接缝类不变量必须经真实调用点断言。

## 2026-09-04 · the one MCP address (batch 5a)

`mcp_port()` (`MCP_PORT`, default 7801), `mcp_base_url()` (`MCP_BASE_URL` for a reverse proxy, else `http://<MCP_HOST>:<MCP_PORT>`), `mcp_mount_path(server_name)` = `/mcp/<server_name>`, `mcp_server_url(server_name)` = base + mount + `/sse`. Every module's `mcp_server` builds its URL from these — no module owns a port, and the agent side (context_runtime → adapters) only ever learns this one base. Codex's adapter still rewrites the trailing `/sse` to the streamable `/mcp` endpoint under the same mount.

## 2026-09-04 · `get_config` is static (batch 5b)

Declared `@staticmethod @abstractmethod`: the platform reads a module's `ModuleConfig` from its CLASS (registry views, display, decision prompt, prefixes) without constructing it. `self.config` is still set in `__init__`.

## 2026-09-04 · `on_instance_activated` hook (batch 5b.2)

Classmethod no-op the narrative instance handler calls when a blocked instance of this module becomes ACTIVE; JobModule overrides it to reschedule the job. The platform names no module.

## 2026-09-04 · a module IS a Capability (batch 5c)

The nine lifecycle methods carry their stage names — `claims_source` (Ingress), `gather` / `contribute_instructions` / `contribute_turn_context` / `contribute_tools` (Assemble), `persist_turn` (Commit), `after_turn` (Reflect); the old names (`hook_data_gathering`, `get_instructions`, `get_turn_context`, `get_mcp_config`, `get_expressive_tools`, `get_disallowed_tools`, `hook_persist_turn`, `hook_after_event_execution`, `owns_working_source`) are gone (rule #2, no aliases). `meta` derives `CapabilityMeta` from `ModuleConfig`; `participations()` answers each MODULE-tier stage with the module itself; `contribute_tools` composes `mcp_server` / `expressive_tools` / `disallowed_tools` into a `ToolSurface` fail-open per part, logging a stale override SIGNATURE loudly (`_is_signature_typeerror` moved here). `LegacyModuleAdapter` is deleted.

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.
