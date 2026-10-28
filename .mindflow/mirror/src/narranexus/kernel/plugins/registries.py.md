---
code_file: src/narranexus/kernel/plugins/registries.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 注释指向改到 `narranexus.contracts.services`

`kernel/plugins/service_refs.py` 已删除（A2-3），三条 ref 搬进契约包。

## 2026-09-04（批 3c.1）— `remove_owner` 跨全部注册表 + 钩子 block；`agent.capabilities.modules` kind 映射

## 2026-09-04（批 3a）— 七个阶段位、profiles、context_providers 的 kind 映射

## 2026-09-03（批 2e）— 每个 `Registries` 默认声明宿主钩子词表

`contracts.events.HOST_EVENTS`（参数取自 payload TypedDict）+ `contracts.agent.events.STAGE_HOOKS` 十四个
阶段钩子。宿主不必记得逐个声明，插件 `backend.hooks` 在任何进程都能对上（hello-world 实锤：忘了声明就整个
插件被隔离）。

## 2026-09-03（批 2a）— `SLOT_KINDS` 补十个平台/内容位

新位的注册表带各自 kind 的契约版本；`backend.hooks` 没有 `Registry`（走 `HookRegistry`），loader 特判。

## 2026-09-03（预审修订）— `SLOT_KINDS`/`_NORMALIZERS` 的框架位路径改为 `turn.pipeline.act.framework`

两张表是路径键，随扩展位树的路径重排一起改；对象身份与语义不变。

## 2026-09-03 — `Registries` 门面：每个扩展位一张注册表，进程内唯一实例

平台代码从这里取注册表（`registry_for(path)`），不再各处私建 dict——批 0 的出口判据
「registries 已被平台消费」是：框架 / provider driver / memory kind 三处都通过**调用时的访问器**
（`framework_registry()`、`driver_registry()`、`memory_kind_registry()`）拿到
`KERNEL_REGISTRIES.registry_for(...)` 本身（`tests/nx_kernel/kernel/test_loader.py` 钉住）。
曾经的三个模块级常量 `FRAMEWORK_REGISTRY` / `DRIVER_REGISTRY` / `MEMORY_KIND_REGISTRY` **已全部删除**：
模块级常量会在 import 期就绑定一张注册表，而注册只允许发生在 boot，两者不相容。注册表按扩展位路径
惰性创建，契约版本与键归一化都来自 `Slot` 自身（`Slot.kind` / `Slot.case_insensitive`），树旁边不再
有第二张路径键表要同步。
`one` 与 `many` 位都用同一个 `Registry`：元数是绑定语义（换/追加），注册表只是「按名字存候选」。
`freeze()` 传播到已建与后建的注册表。`snapshot()` 给出 path→{name→owner} 的确定性视图，
loader 测试拿它和 approval golden 比对。测试自建 `Registries()` 得到干净实例。

## 2026-09-04 · ingress triggers (batch 3c.3)

`SLOT_KINDS["ingress.triggers"] = "trigger"`.

## 2026-09-04 · data-access providers (batch 3c.4)

`SLOT_KINDS["agent.capabilities.data_access"] = "data_access"`.

## 2026-09-04 · services + host hooks (batch 3c.6)

`Registries.services` — one `ServiceLocator` per process (builtins expose at import via `register_all`, user plugins through `PluginContext.services`); `remove_owner` also releases the owner's services.

## 2026-09-04 · channels as descriptors (batch 4a)

`SLOT_KINDS["ingress.channels"] = "channel"`.

Batch 6c: `SLOT_KINDS["kernel.auth"] = "auth"`.

2026-09-07: `Registries.bindings` / `set_bindings()` hold the host's resolved bindings; `SLOT_KINDS` maps `prompt.sections` / `prompt.assembler` to kind `prompt`.

## 2026-09-07 — registry_for is thread-safe

Lazy registry creation is serialised with an RLock (two threads asking first used to be able to create two registries for one slot).

## 2026-09-07 — backend.services kind

SLOT_KINDS maps backend.services to the 'services' contract kind.

## 2026-09-07 — registry_for 读 Slot 自身的 kind/normalize（B7）

SLOT_KINDS/_NORMALIZERS 删除；未知槽抛 UnknownEntry 并在尚无任何注册表时附『是否已 boot』提示（插件声明的槽在 boot 前不存在，裸 Registries() 上取 turn.pipeline.act.framework 正是这种情况）。


## 2026-09-07 — freeze() closes all THREE surfaces (round-2 K2-I6)

`freeze()` only froze the per-slot registries, so after boot a plugin could still
`hooks.add` / `hooks.declare` / `services.expose` — "nothing registers after boot"
was true for one third of the platform. It now also freezes the hook registry and
the service locator, which is exactly the set `remove_owner` withdraws. A slot
whose registry is created lazily AFTER the freeze is born frozen (that branch
existed and is now asserted); `tests/nx_kernel/hosts/test_turn_loaded_for_every_turn_host.py`
drives a real boot and checks every surface, because the suite's process-global
`KERNEL_REGISTRIES` is deliberately never frozen and cannot exercise it.
