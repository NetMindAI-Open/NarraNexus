---
code_file: packages/narranexus-contracts/src/narranexus/contracts/agent/pipeline.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07（批 1 三轮复审移植）— `ActStrategy` 别名的定论

所有 `turn.pipeline.<stage>` 位共享同一个 `StageStrategy` 契约，阶段由 `stage` 字段表达、绑定时校验，不编码进类型；
`ActStrategy` 是给 Act 位一个路径可读名字的别名，**不是**子类型，其它阶段位直接指 `StageStrategy`（builtin.turn 的
manifest 已如此）。写进代码注释。

## 2026-09-03 — `PipelineProfile`：把 fast/voice/job/silent 散开关收成一等对象

一个 profile = 每阶段策略名 + `Budgets` + `CapabilityFilter` + 叙事持久化模式；`TurnOverride`
是回合级覆盖（六层绑定的 TURN 层），`with_override` 合并。`StageStrategy` 是结构化 Protocol
（`stage` + `async run(inputs)`），`inputs` 的形状仍是平台的私有 `StageInputs`，所以整个 kind 标 alpha。
内置 profile id 五个，实现在批 3。
`TurnPipeline`（`turn` / `turn.pipeline` 位的契约：`run(ingress, profile)` 产出平台消息流）与
`ActStrategy`（`turn.pipeline.act` 位，形状就是 `StageStrategy`）在批 1 只为让扩展位树里每个契约符号
真实存在（预审 Important：树引用了不存在的符号）；批 3 的编排器实现前者。

## 2026-09-07（round-2 A2-7）— `PipelineProfile.when` / `.order`: a profile says when it applies

Registering a profile was an open slot; SELECTING one was an if-chain in the platform naming the five
builtin ids, so a plugin's profile could never win a turn unless the caller named it explicitly.
`when` (a tiny closed predicate grammar) plus `order` (lowest first) move that decision onto the
profile as data, and `platform.turn.resolve_profile` became `explicit > best matching when > default`.

The grammar is deliberately a PREDICATE language, not an expression language: truthiness, `!`,
`==`/`!=` against a quoted or bare word, ` and `, ` or `. No attribute access, no calls, no `eval`. A
typo is `WhenSyntaxError` at selection time — never "always true". An unknown context key is falsy so
the host may publish new turn facts without breaking old profiles; an EMPTY clause never matches (the
opposite of the frontend's slot-point `when`, where empty means always — here that would be a profile
silently winning every turn). The frontend grammar (`conversationKind:` / `agentHas:` / `setting:`)
is a sibling with a UI vocabulary; it cannot express a turn fact like `source == 'discord'`.
