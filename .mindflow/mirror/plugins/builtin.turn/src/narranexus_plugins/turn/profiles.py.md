---
code_file: plugins/builtin.turn/src/narranexus_plugins/turn/profiles.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-04（批 3a）— 五个内置 profile

fast/voice/job/silent 从散布各 step 的布尔开关变成**命名的策略选择**：fast → Recall=`narrative_fast`；voice →
Recall=`ephemeral`（持久化 ephemeral）；silent → Act=`silent`；job/default 全默认。`TurnProfile`（schema）继续
承载 Act 阶段读的旋钮（reasoning effort / prompt mode / framework override）——profile 决定「跑哪个策略」，
旋钮决定「怎么跑」。作为 `Contribution` 进 `turn.profiles` 注册表，插件可加自己的 profile。

## 2026-09-07（round-2 A2-7 / G2-I5）— the five profiles carry their own `when`, and the symbol follows §8

Each builtin profile now states the condition under which the host picks it, as data:
`silent`/order 10, `voice`/20, `narrative_strategy == 'bm25_top1' or fast_mode`/30,
`source == 'job'`/40. Those four clauses ARE the if-chain `platform.turn.resolve_profile` used to
hold, in the same precedence — the behaviour is preserved and a plugin profile now competes on equal
terms. `default` deliberately carries no `when`: it is the fallback the host returns when nothing
matched, so a clause that always holds would shadow every other profile.

`PROFILE_CONTRIBUTIONS` → `PROFILES` (API_POLICY §8 for a many-arity slot, and the name
`templates/pipeline_profile` already scaffolds; a third party copying this package and one running
`narranexus plugin new` now get the same symbol).
