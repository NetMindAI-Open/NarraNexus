---
code_file: src/narranexus/kernel/plugins/slots.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

仅注释：ui 位注释由十七个注册表改为十八个（新增 tool renderers），无行为变化。

## 2026-09-07（四轮）— `agent.capabilities` doc 再更正

上一版写「五个能力档的贡献位」却列了六项（含非能力档的 mcp_servers/data_access、漏 skill）；改为「六个子位是贡献位（…）」，
不再把子位数与能力档数混为一谈。`ui` 位注释十六→十七。

## 2026-09-07（批 1 三轮复审移植）— 两条种子位更正

- `agent.capabilities`：契约从值对象 `agent_spec:CapabilitySet` 改回 `namespace_slot`（它是分组位，与兄弟域根一致），
  doc 由「four capability tiers」改为「五个能力档的贡献位（modules / context providers / tools / MCP servers /
  memory kinds / data access）」。
- `model.resolver`：doc 老实写「只声明；helper 客户端直接调纯函数 `resolve_helper_model`，今天没有运行时消费这个位，
  绑定它是 no-op」。默认提供者字符串保留待接线时一并处理。
`turn.pipeline.act` 位由 builtin.turn 的 manifest 声明，其 doc 同步说明 `ActStrategy` 只是 `StageStrategy` 的可读别名。
`docs/plugins/slots.md` 由生成器重生成，`narranexus.toml.example` 两行手工同步。

## 2026-09-07 — 新增 `ingress.message_sources` 种子位（无 kind）

给「不是渠道的消息来源」一个位：消息总线（`builtin.message_bus`）、Job 时钟
（`builtin.job`）。渠道自己的消息来源仍然长在它的 `ChannelDescriptor` 上，所以这个位
只收剩下的那两类。

**为什么 `kind=None`**：`kind` 是 `contracts.API_VERSIONS` 的键，用来给该位的 registry
定契约版本。给它 `kind="channel"` 会逼 `builtin.message_bus` 在 manifest 的 `api` 里为
一个**不是渠道**的东西声明 `channel` 版本；新造一个 `message_source` kind 又等于在契约
词表里加一个永远跟着 `channel` 一起变的版本号。两个都是假信息，所以选择不给 kind，
`MessageSourceSpec` 自己的 `__post_init__` 做结构校验（名字合法、extractor ref 形状对）。
这是「宁可少一个闸门，也不要一个说谎的闸门」。

## 2026-09-04（批 3c.1）— `agent.capabilities.modules`（many，契约 `Capability`）

## 2026-09-04（批 3a）— `turn.profiles`、`agent.capabilities.context_providers`；`turn.pipeline.act` 改 many

阶段位放的是**命名的策略**（profile 点名其一），所以 act 与六个由 `builtin.turn` 声明的兄弟位一样是 many。

## 2026-09-03（批 2a）— 平台服务与内容位

`backend.{routes,tables,workers,settings,hooks}`、`agent.capabilities.{tools,mcp_servers}`、`ui.themes`、
`content.{bundles,skills}` 十个 `many` 位进内核树（owner 内核，alpha）；`backend.hooks` 的契约符号是
`HookImplSpec`。

## 2026-09-03（预审修订）— 阶段位搬到 `turn.pipeline.*` 之下；命名空间自动补全

预审发现 `turn.act` 与 `turn.pipeline` 是兄弟，路径后代关系与「复合位提供者拥有子位」不一致，
嵌套规则在真实树上永远打不到。现在 `turn.pipeline.act` / `turn.pipeline.act.framework` 是
`turn.pipeline` 的后代（v3 spec §6.2 的示意树需同步改成这个写法）。`declare(create_namespaces=True)`
用 `namespace_slot` 自动补出缺失祖先（owner 同声明者），供插件声明自己的扩展点。

## 2026-09-03 — 扩展位树：插件定义的正式基础（spec §6，D25）

`Slot` = 路径 + 契约符号 + 元数（`one` 可替换 / `many` 可追加）+ 默认提供者 + owner + 稳定级别 +
`distribution_only`。树按点分路径组织，**复合位的提供者拥有其子位的定义**：所以
`build_kernel_slot_tree()` 只种根（`kernel.*`、`turn.pipeline`、`turn.pipeline.act.framework`、`model.*`、
`agent.capabilities.memory_kinds`、`ui`、各域根），七个阶段子位 `turn.ingress…reflect` 由批 3 的
`builtin.turn` 声明，nexus_power 的内接缝由 `builtin.frameworks.nexus_power` 声明——放在这里
就会把定义放错 owner。
声明 fail-loud：重复路径 `RegistryConflict`，父位未声明 `UnknownEntry`（一个静默缺失的位会在很久
之后表现为「插件装了但什么都不做」）。`many` 位不允许 `default`（没有「单一默认」的概念，
默认由绑定层的 `=a,b` 表达）。`to_rows()` 是文档生成与工场页的稳定视图。
契约符号是字符串（如 `narranexus.contracts.agent.pipeline:TurnPipeline`）；批 1 起每个符号都真实
存在（`Namespace`/`Kernel`/`services.*`/`ui.Shell`/`ModelResolver`/`TurnPipeline`/`ActStrategy`/
`agent_spec.CapabilitySet`），`test_every_kernel_slot_contract_symbol_resolves` 逐行 import 校验。
`manifest.py` 校验 `provides` 的 key 只看路径存在，`loader` 才解析提供者符号。

## 2026-09-04 · ingress triggers (batch 3c.3)

`ingress.triggers` (many, `TriggerSpec`) under the ingress domain: IM channel listeners (host=channels), pollers run as workers (host=workers), on-demand HTTP servers (host=api).

## 2026-09-04 · data-access providers (batch 3c.4)

`agent.capabilities.data_access` (many, `DataAccessSpec`).

## 2026-09-04 · channels as descriptors (batch 4a)

`ingress.channels` (many, `ChannelDescriptor`).

## 2026-09-04 · modules slot doc (batch 5a)

The `agent.capabilities.modules` meta no longer mentions `mcp_port`; module servers are mounted by path on the single host.

## 2026-09-04 · export list follows batch 5b

`MODULE_METADATA` / `MODULE_DISPLAY_CONFIG` no longer exist (`module_display` exported instead); the modules slot doc says the module's own ModuleConfig is its description.

Batch 6 fix: all seven `turn.pipeline.<stage>` slots are kernel-declared (StageStrategy, many) so every host role loads the builtin.turn strategies at boot instead of discovering the slots at a platform import.

2026-09-07: the prompt domain — `prompt` (root, default builtin.prompts), `prompt.sections` (many, PromptSectionProvider), `prompt.assembler` (one, PromptAssembler, default builtin.prompts).

## 2026-09-07 — backend.services slot

Services a plugin exposes on the locator are a manifest contribution (tuple of (ServiceRef, impl)); they used to be exposed by an import-time side effect of the platform's builtin table.

## 2026-09-07 — Slot 自带 kind/case_insensitive；子槽归属回插件（B7）

Slot 新增 kind（contracts.API_VERSIONS 的键，注册表据此取契约版本）与 case_insensitive（条目名归一化），派生 api_version/normalize；registries.py 的 SLOT_KINDS/_NORMALIZERS 两张按路径键的表删除——槽的事实随槽声明，不再有一张需要与树同步的旁表。SlotTree 新增 roots()（声明序=展示序，根的 doc 即域标题，catalog/文档生成读它，DOMAINS 表删除）、by_kind()、declare_all()（按深度由浅到深声明：深层声明不能先于真实声明把祖先造成 namespace——nexus_power 的 seat 若先于 builtin.turn 处理，turn.pipeline.act 会被造成 one-arity namespace）。内核只播种它自己是权威的槽：七个阶段槽+act.framework+turn.profiles 由 builtin.turn 的 manifest declares，prompt.* 由 builtin.prompts，ui.*（16 个前端注册表）由新的 manifest-only 插件 builtin.ui；原注释『内核声明以保证每个角色 boot 就有』的诉求由 loader 的两阶段 declare 保证。
