---
code_file: src/narranexus/kernel/plugins/bindings.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（二审修订）— 绑定了不存在的扩展位立即报错

`resolve` 先扫描所有层的 key，不在树里的路径（拼错、过时示例）抛 `BindingConflict` 并写明来源层，
不再静默丢弃。

## 2026-09-03（预审修订）— 逗号即列表；嵌套规则在真实树上有回归测试

`_parse_value`：只要含逗号就按列表解析（`a,b` ≡ `+a,+b`），避免 env 里 `a,b` 被当成一个叫
`a,b` 的提供者。嵌套规则以前只在自造树上测；现在 slot 树把阶段放在 `turn.pipeline.*` 之下，
`test_nesting_rule_fires_on_the_kernel_tree` 用 `build_kernel_slot_tree()` 证明「换整个 pipeline
但未 redeclare `turn.pipeline.act.framework` 却绑定它」在启动期报 `BindingConflict`。
「绑定目标是否为已安装且声明了该位的插件」的校验属于批 2（loader 接 registry.json 时）。

## 2026-09-03 — 六层绑定解析：改配置即替换实现（spec §6.4）

`Layer` 是 IntEnum，数字即优先级：DEFAULT < DISTRIBUTION < USER_CONFIG < ENV < AGENT < TURN。
`one` 位取最高层；`many` 位从低到高合并，动词 `+id` 追加、`-id` 移除、`=a,b` 覆盖全序
（列表值或逗号串都接受）。三种解析器：`parse_env`（`NX_BIND__A__B` → `a.b`，双下划线是点）、
`parse_toml`（`[bindings]` 表）、`from_mapping`（发行版 manifest / profile）。
三类冲突全部在解析期抛 `BindingConflict`，绝不静默忽略（宪章 4）：
1. `distribution_only` 位只接受 DEFAULT/DISTRIBUTION 层（`kernel.auth` 这类不能被用户配置或 env 改）；
2. AGENT/TURN 层只许碰 `turn.*` 与 `agent.capabilities.*`（PipelineProfile 的作用域）；
3. 嵌套规则：父位换成非默认提供者后，其未 `redeclares` 的子位若被显式绑定（one 非 DEFAULT 层，
   或 many 有任何层）→ 冲突；只换父位、子位保持默认则静默通过（子位定义随父位提供者，
   §6.3 规则 2/3/4）。
`one` 位既无绑定又无默认 → `UnboundSlot`。结果 `ResolvedBindings` 记录每个位的提供者与来源层，
`write_resolved` 原子写 `bindings.resolved.json` 给工场页与 `narranexus dist doctor` 看。
绑定目标是否真的是已安装并声明了该位的插件，在 `loader` 校验（这里只做层与结构）。

## 2026-09-07 — resolve(strict=False) and ResolvedBindings.unbound

A one-arity slot with neither binding nor default used to raise UnboundSlot out of resolve(), and the host caught it by discarding the ENTIRE resolution — one plugin declaring a defaultless slot silently voided every narranexus.toml / NX_BIND__* / distribution binding. resolve(..., strict=False) records such slots in ResolvedBindings.unbound (also in the JSON snapshot) and installs everything else; strict=True (the default, what tests and tools use) keeps raising. BindingConflict is loud in both modes.

## 2026-09-07 — referenced_plugins(): the one grammar for binding values

A binding value is owner, name or owner:name; many-arity values are lists (or comma strings) with the +/-/= verbs. referenced_plugins() extracts the plugin ids from any of these forms and is what the distribution validator and the bind CLI use — the grammar had been re-derived in three places and the third copy rejected owner:name (the form the CLI writes).
