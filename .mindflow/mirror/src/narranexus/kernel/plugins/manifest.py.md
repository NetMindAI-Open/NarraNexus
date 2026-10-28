---
code_file: src/narranexus/kernel/plugins/manifest.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — `frontend.ui.artifactKinds`

`UiArtifactKind(id, label, downloadExt)`；`UiContributions.artifact_kinds`。前端 loader 据此注册门描述符，
激活事件 `onArtifactKind:<id>`。

## 2026-09-03（批 2d）— `frontend.ui` 声明式 UI 贡献 + `backend.activate` 的启动回落

`UiContributions(pages/panels/commands/themes)`：前端在 import 插件代码之前就按它登记 gate；对应事件
`onPage:/onPanel:/onCommand:` 由 `derive_activation_events` 推导。`backend.activate=True` 而什么事件都推不出时
回落到 `onStartup`（否则这个插件永远激活不了）。

## 2026-09-03（二审修订）— `declares` 允许两种归属

二审指出「只许本插件命名空间」会让 `builtin.turn` 无法声明 `turn.pipeline.recall`、`builtin.ui`
无法声明 `ui.pages`。规则改为：路径在 `<plugin_id>.` 之下，**或**在本插件 `provides` 的某个复合位之下
（与 `_check_redeclares` 用同一条「复合位提供者拥有子位」关系）。

## 2026-09-03（预审修订）— `api` 版本严格相等；`declares` 限本插件命名空间；`hosts` 空=全部

`api[kind]` 必须**等于**宿主版本（bump 即破坏，policy §5「不匹配 fail-closed」）。`declares` 的路径
必须在 `<plugin_id>.` 之下（可归因，且祖先可安全自动补为该插件的命名空间）。`SlotDeclaration`
加 `stability`。`hosts` 空元组的语义「全部宿主」写在字段注释并由 `effective_hosts()` 承担。
`ui.pages` 的激活推导改为精确匹配（不再误配 `ui.pagesomething`）。

## 2026-09-03 — `narranexus-plugin.json` 的模型：声明式、严格、扩展位感知

启动期只读 manifest 不 import 代码（spec §7.4 / §10 性能预算），所以 UI 元数据、装载计划、
拒绝理由全部要从 manifest 得到。pydantic v2 `extra="forbid"` + frozen：未知键是错误（防
拼错静默无效），对象不可变（可放进 loader 的快照）。字段名与 spec §5.1 一致，JSON 侧驼峰
（`displayName/minAppVersion/afterDependencies/activationEvents/distributionOnly`），Python 侧蛇形
（`populate_by_name`）。
`provides` 的 key 是扩展位路径（D25 后取代 `contributes`），校验分两层：模型层只查形状
（路径语法、`module.path:Symbol`），`parse_manifest` 再对着 `SlotTree` 查存在性、元数匹配
（one 给单符号 / many 给列表）、`distribution_only` 位必须由 `distributionOnly` 的 manifest 提供。
本插件自己 `declares` 的位允许出现在自己的 `provides` 里（声明并提供默认实现）。
`redeclares` 必须是本插件所提供复合位的后代且在树里已知（§6.3 规则 3）。`api` 的 kind 必须在
`API_VERSIONS` 且不高于宿主；`minAppVersion` 与宿主版本比较用 `compat.Version`。
`derive_activation_events`：VS Code 1.74 规则的批 0 子集——`ui.pages`/`ui.panels` 推出
`onPage/onPanel:<id>`，其余提供项推出 `onStartup`。`builtin.` 前缀保留，只有
`allow_builtin=True`（内核 `builtins.py`）能用。

## 2026-09-04 · UI slot points (batch 3d.2)

`frontend.ui` gains `conversationKinds`, `messageRenderers` (id + role / contentPrefix gate shape), `timelineEvents` (id + type) and `slots` (id, point ∈ the six slot points, label, `when` validated against the closed grammar, order); `derive_activation_events` adds `onRenderer:` / `onTimelineEvent:` / `onSlot:`.

## 2026-09-04 · on-demand builtin dependencies (batch 3d.3)

`BackendSpec.imports` — import names that prove `pip` is present; on-demand builtins are probed against them.

## 2026-09-07 — PLUGIN_ID_RE tightened; api versions are floor..current

Ids are [a-z0-9] words joined by single '_'/'-' (no doubled, leading or trailing separators) so the flattened id is an injective prefix. _check_api_versions accepts MIN_SUPPORTED_VERSIONS[kind] <= api[kind] <= API_VERSIONS[kind]: a bump opens the deprecation window API_POLICY promises instead of breaking every published plugin in one upgrade; a plugin written against a newer contract than the host is refused ('upgrade the host').

## 2026-09-07 — backend.publicPrefixes

BackendSpec.publicPrefixes: the route prefixes (under the plugin's /api/x/<id>) that serve without authentication; the host registers them at mount time and refuses an auth='none' router outside them.

## 2026-09-07 — plugin-id grammar imported from contracts

PLUGIN_ID_RE comes from narranexus.contracts.distribution instead of a third copy.

## 2026-09-07 — SlotDeclaration 增 kind/caseInsensitive；默认提供者可声明根的子槽（B7）

declares 项新增 kind（校验为契约种类）与 caseInsensitive，透传到 Slot。_check_declares_in_own_namespace 增加树参数：内核根的 default 等于本插件（prompt→builtin.prompts、ui→builtin.ui）时视同该插件 provides 该复合槽，其子槽归它声明——树本身的归属规则（复合槽的提供者拥有其子槽）的直接表达。

## 2026-09-07 — 只有一元复合槽的提供者拥有子槽（round-2 K2-I5）

_check_declares_in_own_namespace 的 under_provided 只认 arity=one 的 provides（或 default 指向本插件的内核根）；多元槽的每个提供者都可声明其子槽会让第二个声明者在 boot 时撞 RegistryConflict。


## 2026-09-07 — `BackendSpec.quotaBypassPrefixes`

`publicPrefixes` 的计费孪生：插件声明「这些前缀仍然要鉴权，但跳过 provider/配额门」，
即免费额度耗尽的用户也必须够得着的配置类端点（宿主自己的 `QUOTA_BYPASS_PREFIXES` 是同
一个理由）。形状和校验与 `publicPrefixes` 完全一致，因为二者由 backend 宿主的同一个
`RoutePolicy` 在**挂载时**处理：越界条目丢弃，manifest 没声明过的运行时 `quota_bypass`
被拒。放在 manifest 里而不是只看 `RouterSpec.quota_bypass`，是因为计费旁路必须出现在用户
批准过的那份声明里，而不是插件运行时自己算出来的值。


## 2026-09-07 — `api` must version every kind the manifest fills (round-2 G2-I2)

`_check_api_versions` walks the keys that ARE in `api`, so a kind the manifest
never names got no version check at all — 8 of 29 builtins were in that state
(the three frameworks, providers, llm_clients, memory_kinds, job, skills), and a
bump of `API_VERSIONS["framework"]` would have loaded them as compatible and
failed deep inside a turn. `slot_kinds_of(manifest, tree)` derives the required
set from `Slot.kind` (the slot decides, not the plugin — that is what makes `api`
a gate instead of a self-description) and `_check_api_covers_slot_kinds` enforces
it: a hard `ManifestError` for builtins (host code, and the template third
parties copy), a WARNING for third-party manifests because tightening validation
on published plugins is a breaking change — `docs/API_POLICY.md` section 4 carries
the window (warning in 1.20, error in 1.21) and `loader.load` puts the line on
`LoadReport.warnings` so the factory page shows it.

## 2026-09-07 — is_builtin_id 收编（round-2 P2-I6）

『是否 builtin』只在 contracts.distribution.is_builtin_id 一处判断（BUILTIN_PREFIX 同处）；九处 startswith('builtin.') 副本全部改调它（distribution_scaffold 的保留命名空间检查是另一个判断，未合并）。
