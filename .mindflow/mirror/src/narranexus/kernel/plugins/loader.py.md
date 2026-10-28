---
code_file: src/narranexus/kernel/plugins/loader.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-04（批 3c.1）— `discover` 读 `builtin_overrides`

`{"builtin.x": {"enabled": false}}` 的内置从加载集剔除并记入 `Discovery.disabled_builtins`；`protected` 的内置
忽略覆盖。

## 2026-09-04（批 3b）— manifest 校验改用 `slot_tree_with_builtins()`（含内置声明的位）

## 2026-09-03（批 2b.5）— `discover` 读 `registry.json`

返回 `Discovery`（manifests + 用户插件路径 + 拒绝原因 + safe_mode）。云端与安全模式只有内置；registry 损坏时
记 error、只装内置（不让宿主起不来）；每条启用记录校验目录存在、manifest 解析（含 minAppVersion）、id 一致、
不在黑名单。

## 2026-09-03（批 2b.4）— `plan_load`：依赖拓扑

`dependencies` 是硬依赖：缺失/版本不满足（`compat.Range`）→ 依赖方 `deps_missing`；环 → 全部成员 `blocked`；
依赖被 blocked 的也 blocked（传递）。`afterDependencies` 只排序、缺失忽略。内置永远在前且不被用户插件重排，
用户插件按拓扑序、同层按 id——`Registry.names()` 跨重启字节稳定的保证。`load()` 把 blocked 写进报告
（error=原因）而不是静默跳过；重复 id 仍是 `ManifestError`。

## 2026-09-03（批 2a）— 声明式贡献与 `backend.hooks`

routes/tables/workers/settings/tools/mcp_servers/bundles/skills/themes 的 `provides` 与其它 kind 走同一条路
（`Contribution` 进对应注册表），宿主批 2a.4 起消费。`backend.hooks` 是唯一特殊路径：值是 `HookImplSpec`
列表，逐个 `registries.hooks.add(name, fn, owner=manifest.id)`；钩子未声明 → `UnknownEntry` → 用户插件被隔离
（写错钩子名不能静默不触发）。

## 2026-09-03（预审修订）— `load_order`、`declares` 先于 `provides`、空贡献可见

`load_order(manifests)`：内置按声明序、用户插件按 id 排序——这才是 `Registry.names()` 跨重启字节
稳定的真正保证（此前只在 docstring 里承诺）。插件的 `declares` 在解析 `provides` 前先进 slot 树
（`create_namespaces=True` 自动补出 `<plugin_id>` 命名空间祖先），所以「声明并提供自己的扩展点」
一次装载即成立。`hosts` 为空按 `effective_hosts()`（= 全部宿主）解释；某个 provides 符号解析出
零个贡献时记 debug 日志（`system.py` 在本地合法为空，但要可 grep）。依赖拓扑排序留批 2。

## 2026-09-03 — 批 0 的 loader：只装内置，云端 fail-closed，import 只发生在这里

`discover(cloud, user_registry_path)` 现在只返回内置 manifest；`cloud=True` 时用户注册表路径
**按构造忽略**（D1 不是配置项，是代码形状），批 2 接 `registry.json` 时这个签名不变。
`load(registries, manifests, role)`：按 `hosts` 过滤角色 → 逐个 `provides` 解析 `module:attr` →
值必须是 `Contribution` 或其可迭代 → `registry_for(path).register_contribution(owner=插件 id)`。
内置失败 raise（内置坏了必须炸，与今天一致）；用户插件失败隔离进 `LoadReport.errors`（批 2 接
crash 计数与自动禁用）。每插件计时进 `PluginLoad.duration_ms`，供 §10 启动预算门使用。
与 import 期注册共存的机制：内置模块在 import 时用同一个 `Contribution` 对象注册，`Registry`
对同名同工厂对象的重复注册是 no-op，所以「先 import 再 load」和「只 load 不 import」得到同一张表
（`test_loading_twice_into_the_process_registries_is_idempotent`）。

Batch 6c: `discover()` rejects a registry.json plugin whose manifest is `distributionOnly` (`incompatible: distribution-only plugin ... cannot be installed at runtime`); such plugins only enter through a distribution.

## 2026-09-07 — an isolated plugin leaves no partial registrations

load() withdraws a non-builtin plugin's contributions and hooks (registries.remove_owner) when a later symbol fails to resolve — before this the routes/triggers/tools registered before the failure kept being served while the report said 'isolated'.

## 2026-09-07 — backend.services installer

SERVICES_SLOT entries are exposed on Registries.services under the manifest id (released with the owner), the same shape as the hooks special case.

## 2026-09-07 — 两阶段：先声明所有槽，再注册贡献（B7）

load() 第一遍遍历（同时查重 id）收集本角色所有 manifest 的 declared_slots 并 declare_all（由浅到深）；第二遍才 provides。动机：builtin.frameworks.* 在 BUILTIN 顺序里先于 builtin.turn，却要向 builtin.turn 声明的 turn.pipeline.act.framework 注册——加载顺序不再决定槽是否存在。

## 2026-09-07 — 有界导入 + 阶段 deadline；service 二次暴露是冲突（round-2）

resolve_symbol 接受 importer；非 builtin manifest 用 _bounded_importer（自己的包走 import_plugin_module(timeout)，其它照常）；PluginImportTimeout → PluginLoad.slow=True；deadline 在每个用户插件前复查。_expose_services：同 owner 重复暴露是 no-op，不同 owner 抛 RegistryConflict（旧代码 try_require 为真就静默跳过并计数）。


## 2026-09-07 — LoadReport.warnings (round-2 G2-I2)

Non-fatal manifest findings now have a home on the report instead of only the log.
The first entry is the `api`-does-not-cover-its-slot-kinds deprecation window
(docs/API_POLICY.md section 4): builtins are refused at parse time, third-party
manifests get one line here per load until the window closes. It is filled from
`manifest.missing_api_kinds` against the registries' own slot tree, so the check
sees the slots the plugins in THIS load declared.
