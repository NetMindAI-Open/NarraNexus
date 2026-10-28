---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/assembly.py
last_verified: 2026-09-08
stub: false
---

## 2026-09-08 — `mcp_spec_from_config`：两处 dict→spec 收成一个

回合 `mcp_servers` 与 expandable 的 `mcp_servers` 此前各自 `McpServerSpec(url=...)`，stdio 配置被读成空 url。现在同一个
转换函数按 `command` 有无分流；测试 `test_mcp_channel_runs_a_stdio_server_with_the_host_interpreter` 覆盖两形态。

## 2026-09-03（批 2a.5）— dispatcher 接 `deferred_tools`

## 2026-08-24 — wait 座位接线 + `_steer_channels`(**steerable-flag** 门,非 inlet 身份)

`run_turn_events` 里构造共享 `WaitState()` 并 `LoopAssembly(wait=...)` 穿进去——`wait_for_input` 工具写它、loop 在 WAIT 边界读它,是"工具→loop"的唯一接线点(同 expression_nudge 教训)。post_init 缺省挂空 `WaitState`。**`wait: WaitRequest` 只让构造点被类型检查**;loop 侧经 `Any` assembly 读 `a.wait.pending`,静态查不到,故 clamp 靠运行期 `WaitRequest.request`(见 [[protocols.py]] I2)。
**`_steer_channels(steerable: bool, wait_state)` 纯函数**(模块级、可单测):仅当 **`TurnOptions.steerable` 为真**才暴露 `WaitChannel`。**关键订正**:门**不能**判 inlet 身份——默认的 subprocess `runner.main()` 每轮无条件挂一个 `QueueSteeringInlet`(只在可控轮被喂),所以"有没有挂 inlet"恒为真、`steering is None` 在生产上永不成立。steerable 由 orchestrator 是否注册 `SteerChannel` 决定(`nexus_agent._build_request_payload` 写 `steer_channel is not None`),经 `TurnOptions.steerable` 跨序列化边界带过来。非可控轮暴露该工具=agent 一调就在无人喂的队列上阻塞满 clamp(缺省 60s、最长 300s)。DRAIN 与此正交:空 inlet 无论可控与否都 drain 成空。刻意不做注册表。回归打**生产臂**:`test_steering_wiring.test_wait_tool_is_hidden_on_a_non_steerable_run_even_with_an_inlet_mounted`(挂真 inlet + steerable=False → 工具不可见)+ `test_wait_for_input.test_steer_channels_are_gated_on_the_steerable_flag`。

## 2026-08-21 — steering inlet 接线(live 注入的顶层入口)

`run_turn_events` 新增 `steering: SteeringInlet | None = None`,原样接进
`LoopAssembly(steering=...)`;`None` 由 assembly 的 post_init 挂 `NullSteeringInlet`
（现状不变）。与 expression_nudge 那条同理:这是"活的 steering inlet → loop"的唯一接线点。
关键区别——steering 是**活对象(带队列),不走 TurnRequest 的 JSON 序列化**:transport 层
（本地 runner / 云端 executor)在进程内构造并喂它,顶层入口只负责穿线。drain 行为见
[[loop.py]] DRAIN_STEERING + [[steering.py]] QueueSteeringInlet。
（附带订正:上条"run_turn_events 无测试入口"已不再成立——`test_steering_wiring.py`
用 monkeypatch 假 loop 驱动 run_turn_events,锁住这条接线。）

## 2026-08-17 — 动态尾巴里，来源声明领在回复提醒之前

`_tail()` 从 `(plan, reminder)` 变成 `(plan, origin_declaration, reminder)`。两者都放
动态尾巴，因为都是每轮的事实；来源声明在一轮之内不变，但它必须和它所修饰的那条规则同行，
两者才不会被分开读到。

声明本身由 step 层渲染并经 `TurnOptions.origin_declaration` 传入——本框架**不合成**它，
所以它和 CLI adapter 发出的是同一串字符。


## 2026-08-13（五审）— is_expressive 接入 ToolDispatcher

dispatcher 的 expressive 保底席判据来源：`is_expressive=expression.is_expressive`
与 marker_tools 并列传入——expression 在 build 段更早处构造，顺序安全；传活对象
（不是 frozenset 快照），expansion 轮内 add_tools 的授予才可见。
**测试覆盖缺口（诚实标注）**：run_turn_events 无测试入口（全仓 tests 不驱动它），本行接线的正确性目前靠人工逐跳核对（六审已验通：expressive_tools → mcp_tool_name → is_expressive 三处拼写一致）；smoke 补法记 reference/self_notebook/todo/。

## 2026-08-13 — expression_nudge 接线

LoopAssembly 新增 `expression_nudge: bool = False`；`run_turn_events` 把
`opts.expression_nudge` 接进 assembly——这是 TurnOptions→loop 的唯一接线点
（同 2026-08-07 确权链路教训：缺这一行前面三处都不生效）。机制见 loop.py.md 同日条目。

## 2026-08-10 (review 修正) — 字段改名 `extra_readable_roots` → `extra_accessible_roots`

纯改名，语义不变：这份授予同时管写与删（confinement 层检查 `file_path` 与 shell 路径），
旧名名不副实。详见 [[policy.py]]。

## 2026-08-07 — `extra_readable_roots` 接到 ToolContext 上

`TurnOptions.extra_readable_roots` → `ToolContext`，供两个 confinement layer 消费
（见 [[policy.py]]）。**这里是整条确权放开链路的唯一接线点**：契约在
[[options.py]] / [[tooling.py]]，判定在 [[policy.py]]，而把值真正交到 ToolContext 手上的
是本文件。改动只有一行，但缺了它前面三处都不生效。

## 2026-08-06 — voice fast mode: TurnProfile 管道（缺省=现状）

PromptAssembler 的模式来源从硬编码 PromptMode.FULL 改为 PromptMode(opts.prompt_mode)——默认值不变，快速模式可按 turn 降面。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

ExpressionContract 前移到 expander 之前构建并挂 `add_expressive` 缝;
LoopAssembly.expression 直接用它(不再 frozenset 二次构造)。reminder 从装配时
算死的字符串变 `_tail()` 闭包:每步 `NexusPowerPrompts.reply_reminder(
expression.names())`——中途展开出的投递工具进 reminder,稳定前缀不动。
`PromptInputs.default_reply_tool` 取 `expression.names()` 首位(initial
expansions 之后取值,起跑展开授予的回复工具也算),装配时冻结。

# assembly — 唯一装配点:TurnRequest 进、类型化事件流出

LoopAssembly 是循环的全部依赖(硬组件无默认、策略缝带默认,R1:装配复杂度有意集中于此文件);run_turn_events 是框架顶层入口:装配→初始展开→跑循环。TurnRequest 整包可 JSON 序列化,这是 runner 跨进程传输的前提。测试换件分两档:loop 层用 dataclasses.replace(assembly 可注入);**顶层 run_turn_events 自己造 assembly、调用方注入不进去,入口测试改为 patch 它函数体内的懒导入符号——因此这些函数内导入必须保持懒加载**(谁把 NexusPowerLoop/LiteLLMModelClient 提到模块顶层,入口测试的 patch 就拦不住,真 loop 会去打真 provider)。坑:harness system 消息插在平台前导 system 段末尾(_insert_harness),不能追加在 user 之后;output_schema v1 显式 fail loud(schema 诚实)。

## 2026-09-04 · extension points (batch 3d.1)

The strategy seats (expression, policy layers, projector, compaction, stop) are no longer constructed here: `run_turn_events` builds a `SeatContext` and asks `extension_points.resolve_one/resolve_many` for each, so a plugin provider or an `NX_BIND__` binding replaces a seat without touching the assembly. Defaults are the classes this file used to instantiate, in the same order (policy layers) — behaviour unchanged; `LoopAssembly` also receives `stop` explicitly now.

## 2026-09-07 — mandatory safety layers

_with_mandatory_layers puts DisallowedTools / WorkspaceConfinement / ShellConfinement first and appends the seat's bound layers (deduplicated by type): a binding can add policy, never remove the executor-escape mitigations.
