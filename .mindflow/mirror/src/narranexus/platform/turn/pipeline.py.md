---
code_file: src/narranexus/platform/turn/pipeline.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-04（批 3b）— `resolve_profile(explicit=)`

回合显式点名的 profile（TURN 绑定层）优先级最高，必须在 `turn.profiles` 注册表或内置表里，否则 `UnknownEntry`。

## 2026-09-04（批 3a）— `TurnPipeline`：七阶段唯一的编排器

对每个阶段：`onWill<Stage>` 钩子（收冻结输入视图；返回值改写留给后续批）→ profile 指定的策略
（`turn.pipeline.<stage>` 注册表按名取，没人提供即 `UnknownEntry` 炸响）→ `onDid<Stage>`（冻结输出视图）。
阶段消息原样流给调用方；钩子受 profile 同步预算限制且永不让回合失败；Ingress 置 `aborted` 则停。
`resolve_profile` 把遗留旗标映射到内置 profile：silent > 显式 TurnProfile 名（含 voice→voice，bm25→fast）> fast_mode
> 来源 job > default，先查 `turn.profiles` 注册表再回落内置表。`PIPELINE_CONTRIBUTION` 填 `turn.pipeline` 位。

Batch 6b.2b: profiles are registered lazily from the `builtin.turn` manifest when the registry is empty; an unknown profile raises `UnknownEntry`.

## 2026-09-07 — binds the turn Event at every stage boundary and yield

run() calls services.bind_event(event_id) exactly once, as soon as any stage has set ctx.event — checked after every yielded message and after every stage, not on the first yield (Recall/Compose run helper LLMs and yield nothing). This is how the runtime attributes spend and logs to the turn without the pipeline knowing about ExitStacks.

## 2026-09-07 — dead except removed

UnknownEntry subclasses KeyError, so the try/raise/except KeyError in resolve_profile always took the second branch; collapsed to one raise.

## 2026-09-07 — TurnPipeline declares slots only; profiles come from the boot

__init__ declares the seven stage slots for a hand-built tree and nothing else; resolve_profile no longer populates turn.profiles on first use.

## 2026-09-07（round-2 G2-I3 / A2-7 / A2-10）— selection stays, implementation left

Two changes with one theme: the platform keeps the parts that are about the TURN, and gives up the
part that is about one implementation.

* `TurnPipeline` moved to `plugins/builtin.turn/src/narranexus_plugins/turn/pipeline.py`. It was the
  only one of the 95 builtin `provides` refs pointing outside its own package, and it put
  `Contribution("builtin.turn", ...)` — a plugin id — in platform source. `turn_pipeline_for()` reads
  the `turn.pipeline` binding instead, so excluding `builtin.turn` really removes the implementation
  and a third party can actually fill the slot.
* `resolve_profile` is no longer an if-chain naming the five builtin profile ids. It builds a fact bag
  (`turn_when_context`: `silent` / `fast_mode` / `voice` / `narrative_strategy` / `source`) and asks
  every registered profile's own `when` clause, lowest `order` winning; `explicit` still outranks
  everything and `default` is still the fallback. Registering a profile was already an open slot;
  SELECTING one was not, so a plugin's profile could only ever run if the caller named it. The five
  builtin clauses reproduce the old chain exactly (`silent`/10, `voice`/20,
  `narrative_strategy == 'bm25_top1' or fast_mode`/30, `source == 'job'`/40).

The fact-bag keys are the contract with profile authors: adding one is additive (an unknown key in a
`when` clause evaluates falsy), removing one is breaking.
