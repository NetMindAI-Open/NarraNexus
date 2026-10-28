---
code_file: src/narranexus/kernel/plugins/builtins.py
last_verified: 2026-10-14
stub: false
---

## 2026-10-14 — the builtin toggle rules live here, once

`check_builtin_toggle(id, enabled, excluded=…)` is the single rule set for switching a builtin through `registry.json`
`builtin_overrides`: `KeyError` for an unknown id, `RegistryError` for a builtin the running distribution excludes (never
loaded: an override would be a silent no-op reported as done; wins over `protected`) and for disabling a protected one.
`refuse_if_excluded` is the excluded half alone (on-demand dependency install). Callers: `backend/plugins_factory/service.py`
(passes its `BootReport.excluded_builtins`, so the running backend never resolves twice) and `narranexus/cli/main.py`
(`excluded=None` → `excluded_builtins_of_running_distribution()`, which `resolve_from_env`s `NARRANEXUS_DIST`; a broken
distribution raises instead of returning "nothing excluded"). Add a new toggle rule here, not in a caller. The CLI still does
not cascade a disable to dependants (the service does) — a known, separate gap.

## 2026-09-23 — 可选内置浏览器

`builtin.browser` 在统一内置目录登记，manifest 和模块仍由独立的
`narranexus_plugins.browser_module` wheel 提供。注册不启动 Chromium，也不下载运行时；
发行配置和禁用状态决定贡献是否进入宿主。浏览器进程只在实际操作时由平台服务启动。

## 2026-09-04（批 3c.1）— 每个内置模块一份 manifest（17 份）

`builtin.chat/awareness/basic_info/social_network/job/skills/message_bus/common_tools/general_memory/home_assistant`
+ 六个 `builtin.channels.*`，各 provides `agent.capabilities.modules: PLUGIN_<ID>`；`builtin.nexus_plugins_module`
补 provides。`test_disable_builtin_degrades_cleanly` 逐个禁用验证。

## 2026-09-04（批 3b）— `slot_tree_with_builtins()`

用户插件的校验树 = 内核树 + 每个内置 manifest `declares` 的位（如 `builtin.turn` 的六个阶段位）；否则第三方
Recall 策略永远装不上（hello-world 实锤）。installer/discover/publish-check/自我扩展 validate/工场都改用它。
装饰器教训再犯一次：在 `@lru_cache` 与 def 之间插函数会把缓存装到新函数上。

## 2026-09-04（批 3a）— `builtin.turn`

提供 `turn.pipeline`（编排器）与七个阶段的默认策略、五个 profile；`declares` 六个阶段子位（act 已在内核树）。
它是「复合位提供者拥有子位」规则的第一个真实用户。

## 2026-09-03（批 2f.1）— `builtin.nexus_plugins_module`（protected，无 provides）

模块本身是插件；`protected: true` 让工场与自我扩展工具都不能改它。

## 2026-09-03（批 1）— 新增 `builtin.llm_clients`

指向 `agent_framework.llm.helper_sdk:CONTRIBUTIONS`（anthropic/openai/cli 三个协议客户端），宿主
backend/mcp/workers。

## 2026-09-03（预审修订）— 框架位路径改为 `turn.pipeline.act.framework`；缓存拆分

`builtin_manifests()` 零参 `lru_cache`，`build_builtin_manifests(tree)` 不缓存，避免用可变树做缓存键。

## 2026-09-03 — 内置插件清单（显式注册，唯一真源）

D4「内置即插件」的落点：六份 manifest 常量（批 1 加 `builtin.llm_clients`）——三个框架（各一个插件，`nexus_power` 常驻、
`claude_code`/`codex_cli` 依赖 `on_demand`，吸收 D7 的安装表语义）、`builtin.providers`（九个 driver
的 `CONTRIBUTION`，`system` 给 `CONTRIBUTIONS` 在本地为空）、`builtin.memory_kinds`。
`provides` 指向遗留模块里的 `Contribution` 常量（`agent_framework:NEXUS_POWER`、
`drivers.netmind:CONTRIBUTION`、`memory.specs:CONTRIBUTIONS`），而不是指向类或工厂——命名规则
（框架名、`driver_type()`、`kind`）留在各自领域，内核不学任何 kind 的取名法。
选择显式清单而非目录扫描（参考文档 §E-24：确定性、可 grep、启动快）。`builtin_manifests()`
带 `lru_cache`，因为数据是常量；批 3 逐个抽取内置时，这里每插件一条。`hosts` 目前只按进程角色
粗分（框架只在 backend 装），`mcp`/`workers` 装 providers 与 memory kinds。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

First feature-level builtin: `builtin.teams` (hosts backend; provides `backend.routes` + `backend.workers`; depends on message_bus + chat so disabling either cascades). It is the template for extracting the remaining features — routes/worker arrive through contributions, the platform keeps no direct reference.

## 2026-09-04 · ingress triggers (batch 3c.3)

Six channel builtins, builtin.job and builtin.chat now also provide `ingress.triggers` (`module.contributions:TRIGGERS_*`); builtin.chat provides `backend.hooks` (`chat_module.plugin_hooks:HOOKS`).

## 2026-09-04 · data-access providers (batch 3c.4)

awareness/social_network/basic_info/job/chat also provide `agent.capabilities.data_access` and their `backend.routes` twins (`backend/routes/agents/{awareness,profile,social_network,narrative,jobs,chat_history}:ROUTES`).

## 2026-09-04 · plugin-owned router (batch 3c.5)

Channel builtins provide `backend.routes.channels.<ch>:ROUTES`; builtin.job adds `backend.routes.jobs:ROUTES` + `backend.routes.dashboard.jobs:ROUTES`; builtin.skills `backend.routes.skills:ROUTES`; builtin.home_assistant `backend.routes.home_assistant:ROUTES`.

## 2026-09-04 · services + host hooks (batch 3c.6)

builtin.job / builtin.awareness / the six channel builtins provide `backend.hooks` (`<module>.plugin_hooks:HOOKS`).

## 2026-09-04 · extension points (batch 3d.1)

`builtin.frameworks.nexus_power` declares its five seat slots (`builtin.frameworks.nexus_power.{stop,compaction,projector,expression,policy}`, contracts = the NexusPower protocols) and provides their default implementations from `nexus_power/extension_points.py` (one-arity seats as a single Contribution symbol, the policy seat as a tuple).

## 2026-09-04 · on-demand builtin dependencies (batch 3d.3)

`builtin.channels.lark` declares `backend.pip` lark-oapi + `imports` lark_oapi with `install.deps: on_demand` (still in the base install today; see install/builtin_deps.py).

## 2026-09-04 · channels as descriptors (batch 4a)

The six channel builtins and builtin.home_assistant provide `ingress.channels` (`<module>.descriptor:CHANNEL`).

## 2026-09-04 · telegram / slack / discord provide no routes (batch 4d.3)

Their bespoke routers were retired (the generic `/api/channels` router serves them); the manifests keep modules, triggers, hooks and the channel descriptor. lark / wechat / narramessenger still provide their channel-specific routers (OAuth, QR, prewarm).

## 2026-09-04 · builtin module packages live under plugins/ (batch 6b)

Each module builtin is a uv workspace member at `plugins/<id>/src/narranexus_plugins/<pkg>/` with its own `pyproject.toml`, `narranexus-plugin.json`, `api.py` facade, README, CHANGELOG and tests. (Superseded below: the on-disk JSON is the manifest's ONLY home — this file holds no copy — and the platform-side contribution table `module_system.contributions` was deleted; each plugin owns its own `contribution.py`.)

## 2026-09-04 · `register_builtin_provides(slot)` (batch 6b.2)

A platform package that needs one slot populated at first use (memory kinds, provider drivers) asks the kernel to register every builtin manifest's contributions for that slot — the manifests name the code, the loader resolves it, the platform never imports a plugin by name. `builtin.memory_kinds` and `builtin.providers` are workspace packages now (`narranexus_plugins.memory_kinds` / `narranexus_plugins.providers`).

Batch 6b.2b: `register_builtin_provides(slot, registries=None)` resolves a slot's contributions from the builtin manifests and registers them — the seam platform code uses so it never imports `narranexus_plugins`. Framework/turn/llm-client refs now point at the plugin packages.

Batch 6b.3: builtin.teams provides two routers, the worker and a `backend.hooks` implementation, all under `narranexus_plugins.teams`.

Batch 6c: two authProviders builtins `builtin.auth.local` and `builtin.auth.netmind` (distributionOnly, protected, provide `kernel.auth`).

Batch 6 fix: builtin.turn and the three frameworks list hosts backend+mcp+workers (every turn-running process); `register_builtin_provides` raises a clear RuntimeError when the registries are frozen and the slot is empty (the manifest must list the role) instead of RegistryFrozen mid-turn.

2026-09-07: builtin.prompts manifest (hosts backend/mcp/workers; provides prompt.sections + prompt.assembler).

## 2026-09-07 — register_builtin_provides never replaces; duplicate block removed

The lazy builtin registration registered with replace=True, which could silently overwrite a user plugin's same-named entry depending on import order; idempotency only needs the same-object / same-owner no-op the registry already provides, so replace is gone. The duplicated registries-resolution block is collapsed to one.

## 2026-09-07 — lazy registration honours the distribution; providers declares its dependency

register_builtin_provides filters BUILTIN_MANIFEST_DATA by the process distribution's picks (NARRANEXUS_DIST, cached per path): an excluded builtin — an excluded AUTH provider in particular — no longer resurrects through the lazy ensure_* path. builtin.providers now declares dependencies on builtin.llm_clients (it imports its api), so dist doctor sees the coupling.

## 2026-09-07 — load_builtins() replaces the lazy register_builtin_provides

Registration happens only at boot. load_builtins(registries, role, distribution=) registers every builtin's contributions for a role into a Registries — what hosts.boot does in stage 1, available to private registries (tests, an embedding host) without discovery, marker or write-back. register_builtin_provides (the per-slot lazy path seven platform seams called, which ignored the distribution and could overwrite a user plugin's entry) is gone.

## 2026-09-07 — the manifest has one home: plugins/<id>/narranexus-plugin.json

The 338-line BUILTIN_MANIFEST_DATA dict (a byte-for-byte copy of 28 JSON files, kept in sync by 28 parity tests) is gone. BUILTIN_PLUGINS is the ordered (plugin id, package) list — load order and where the manifest ships; _manifest_path reads the JSON from the package (every plugin wheel force-includes it) or the source checkout; BUILTIN_MANIFEST_DATA is now the parsed files. Adding a builtin = a directory + one line here (until the list is derived too); the kernel no longer names any plugin module path.

## 2026-09-07 — builtin manifest 校验前先声明全部 builtin 槽（B7）

build_builtin_manifests 第一遍用 Manifest.model_validate（不需要树）取每个 builtin 的 declared_slots 并 declare_all 进树，第二遍才 parse_manifest——否则 nexus_power 的 provides['turn.pipeline.act.framework'] 会在 builtin.turn 之前被判为未声明。slot_tree_with_builtins 同样走 declare_all。BUILTIN_PLUGINS 新增 (builtin.ui, ui)。

## 2026-09-07 — manifest 首次使用才读，缺包可容忍（round-2 K2-I2/G2-C1）

BUILTIN_MANIFEST_DATA 由模块 __getattr__ 惰性给出（builtin_manifest_data() lru_cache）；某 builtin 包不存在（wheel 发行版只装子集）时跳过并进 missing_builtins()，不再 FileNotFoundError 杀进程。


## 2026-09-07 — reading a manifest LOCATES the package, never imports it (round-2 G2-C1(c))

`_manifest_path` used `importlib.resources.files(f"narranexus_plugins.{pkg}")`, which IMPORTS the package it is asked about: reading 29 JSON files pulled 106 plugin modules (~1.2 s) into every process that so much as touched the kernel, registered their message-source handlers before any distribution filter ran (making `excludes` cosmetic), and was invisible to import-linter because the module name is a string. It now uses `importlib.util.find_spec(...).submodule_search_locations` — the package directory without executing it — and falls back to the source checkout as before. After a full `builtin_manifest_data()` the only `narranexus_plugins.*` entry in `sys.modules` is the bare namespace package, which holds no code; `tests/nx_kernel/kernel/test_import_side_effects.py` asserts exactly that in a clean subprocess.
