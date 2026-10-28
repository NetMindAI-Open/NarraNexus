---
code_file: src/narranexus/hosts/boot.py
last_verified: 2026-09-08
stub: false
---

## 2026-09-08（复审）— 恢复时清崩溃预算；注册表每次 boot 只读一次

crashed/slow 走回 registered 后调用 `store.clear_crashes`，否则数月后一次无关崩溃就触发两次阈值自动禁用。`store.read()`
提到循环外（注册表损坏时视为无记录，不影响只装内置的降级路径）。

## 2026-09-08（本地 E2E 实测）— 崩溃后的一次干净 boot 要能走出 `crashed`

状态机只允许从异常态回到 `registered`，而 boot 末尾对干净加载的插件直接 `→ validated`，`RegistryError` 被 debug 吞掉：
一个曾经崩过、现在加载正常的插件在工场里永远显示 crashed + 旧错误。现在检测到 `crashed`/`slow` 先回 `registered` 再往前走。

## 2026-09-04（批 3c.1）— 禁用的内置：在冻结前 `registries.remove_owner`

import 时登记的贡献（模块类等）在 stage1 之前按 owner 移除并 block 其钩子，`BootReport.disabled_builtins` 记录。

## 2026-09-03（批 2b.5）— 分阶段启动（spec §9.2）

一个进程角色一次：stage1 内置（`load()` 内部失败即 raise——内置坏了必须炸）；stage2 用户插件
（`discover` 读 `registry.json`，云端/安全模式只返回内置；拒绝项 missing/incompatible/blocked 写回状态）：
每插件先装合成包与私有依赖 finder（不执行代码）→ `load()` 登记声明式贡献（失败隔离 + `record_crash`）→
插件表经宿主给的 `register_table` 注册（纯数据，不激活也建表，所以 backend 在 `auto_migrate` 之前调 boot）→
激活器记下事件 → 状态推进到 `enabled`；最后 `registries.freeze()`。启动标记：本地在 stage2 前 `enter`，宿主
健康后 `mark_healthy()` 才 `exit`；连续两次残留 → 写 `safe_mode` 并只装内置。一切决定都回到 `BootReport`
（工场页要展示），不只写日志。

## 2026-09-04 · on-demand builtin dependencies (batch 3d.3)

Before stage 1 every builtin passes `ensure_builtin_deps` (probe / install on the local build); a builtin whose on-demand dependencies are unavailable is recorded in `BootReport.deps_missing`, its contributions removed, and the boot continues without it.

Batch 6c: `boot(..., distribution=DistributionResolution | None)`. With a distribution the stage-1 set is its plugin set: builtins it leaves out lose their import-time registrations (`report.excluded_builtins`), bundled path plugins get a synthetic package + private deps via `_prepare_user_plugin` and load fail-fast in stage 1, and `runtime.userPlugins=false` skips the user registry. A resolution with problems raises before anything loads. `report.distribution` names the distribution.

Batch 6c.3: `prepare_bundled_plugins(distribution, store, skip)` is the shared helper (boot and the backend's import-time registration) giving bundled path plugins their synthetic package and deps.

## 2026-09-07 — inspect mode, corrupt-registry tolerance, crash accounting order, real stage-2 deadline, LKG at health

boot(inspect=True) discovers and registers exactly like a real boot but writes nothing: no BootMarker (three 'narranexus slots' runs used to push the app into SAFE MODE with a fabricated reason), no rejection/crash/state persistence — the CLI's booted_registries uses it. A corrupt registry.json no longer raises out of boot (the two bare store.read() calls are guarded; builtins boot, safe mode / rollback stay reachable). record_crash runs after EVERY isolation source (load errors and refused tables) so a table refused every boot reaches the auto-disable threshold. stage2_deadline_s is enforced: plugins the deadline cuts off go to report.slow with state 'slow' (never crash_count), and a PluginImportTimeout during prepare is classified slow too. mark_healthy() now also snapshots registry.json to registry.lkg.json — the only moment a state is proven bootable — so BootReport carries the store.

## 2026-09-07 — 阶段 2 真有上限；捆绑插件也有生命周期（round-2 K2-C1/C2/I4）

load() 现在带 import_timeout_s+deadline：用户插件的每个 nxplugins.* 导入走 importer.import_plugin_module 的守护线程门，超时记 slow（不算 crash、撤回其部分注册），所以一个 import 卡死的插件不再拖垮健康检查窗口（旧实现的 deadline 只包住不跑插件代码的 prepare 循环）。表注册/activator.register/状态回写从 if users: 里提出来，作用于『本次 boot 装载的所有非 builtin』= 发行版捆绑插件 + 运行期插件（example-tob 的 acme.crm 以前永远不激活）；捆绑插件不计 crash。registry.json 只读一次（_read_registry），损坏时捆绑插件仍能 boot。写回记账段在 lifecycle 为空时也执行（全部被 deadline 拦下时仍要记 slow）。


## 2026-09-07 — a refused table withdraws the plugin's registrations (round-2 T2-C1)

`register_table` refusing a `TableSpec` is an isolation, but the post-load table
loop only wrote `report.isolated[owner]` and returned: the plugin's routes stayed
mounted, its hooks kept firing and its services stayed resolvable while the report
said it was isolated. Because the refusal happens AFTER `load()`, this was
owner-controlled — ship a colliding `TableSpec` and keep a live route that no
report lists. The `except` now calls `registries.remove_owner(owner)` (registry
entries + `hooks.block` + `services.release_owner`, the same three things a load
failure withdraws), guarded so a rollback failure logs instead of killing the
boot, and reports how many registrations went. Builtins never enter this loop
(they are skipped by owner prefix) and keep failing fatally in `load()`.
`tests/nx_kernel/hosts/test_boot_hardening.py::test_a_refused_table_withdraws_everything_the_plugin_registered`
asserts on `registries.snapshot()` / hooks / services, not on the report.

## 2026-09-07 — is_builtin_id 收编（round-2 P2-I6）

『是否 builtin』只在 contracts.distribution.is_builtin_id 一处判断（BUILTIN_PREFIX 同处）；九处 startswith('builtin.') 副本全部改调它（distribution_scaffold 的保留命名空间检查是另一个判断，未合并）。
