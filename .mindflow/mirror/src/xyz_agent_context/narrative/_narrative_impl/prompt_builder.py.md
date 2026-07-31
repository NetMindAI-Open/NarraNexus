---
code_file: src/xyz_agent_context/narrative/_narrative_impl/prompt_builder.py
last_verified: 2026-07-28
stub: false
---

## 2026-07-28 — R4d：created_at 迁 turn 半（稳定半不再含任何时间戳）

- `build_main_prompt(include_volatile=False)` 不再向 STABLE 模板传
  `created_at`；`build_turn_prompt` 增加 `created_at=_canonical_timestamp(...)`。
- 理由见 [[prompts.py]] R4d 条目：created_at 的**取值**来自两个时钟
  （[[narrative_repository.py]] `_entity_to_row` 不写该列 → INSERT 取 DB 默认
  `(datetime('now'))`；`crud.py` 的内存对象取 save 之前捕获的
  `datetime.now(timezone.utc)`），R4c 的 `_canonical_timestamp` 统一了格式却
  无法统一时钟，残留差异是**等长替换**，字节计数类诊断完全看不见。
- `_canonical_timestamp` 本身**未改**——它仍是唯一的时间戳格式化入口，MAIN /
  TURN 两条渲染路径都走它；STABLE 路径现在根本不渲染时间戳。
- 测试：`test_stable_block_contains_no_timestamp_at_all`（稳定半不含
  `Created At` / `Updated At` / ` UTC`）、
  `test_stable_block_identical_across_the_two_created_at_clock_sources`
  （Python 时钟轮 vs DB 时钟轮字节相同，且 turn 块仍各自带着自己的创建时间）、
  `test_the_two_clock_sources_really_do_render_different_bytes`（守住前提：
  两个时钟确实渲染出不同且**等长**的字节）。

## 2026-07-28 — R4c：时间戳单一规范化渲染 + Name 迁 turn 半

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

- 新增模块级 `_canonical_timestamp(value)`：同一时刻经两条路径到达 prompt 时
  字节曾不同——内存新建 narrative 带微秒 tz-aware datetime，DB 回读是秒级
  （naive 或 tz-aware 视 backend/driver）——`str()` 渲染出
  `…:39.367468+00:00` vs `…:39+00:00`，在前缀 ~1.2K 处打穿缓存（E2 §3 第一
  分歧字节）。规范形：UTC、秒级、`YYYY-MM-DD HH:MM:SS UTC`；naive 视为 UTC
  （全库写入均为 UTC）。created_at/updated_at 在 MAIN/STABLE/TURN 三条渲染
  路径全部走它——**单一格式化路径**，不存在第二个时间戳序列化点。
- `build_main_prompt(include_volatile=False)` 不再渲染 Name；
  `build_turn_prompt` 增加 name。理由见 [[prompts.py]] R4c 条目（updater 每轮
  重写 name，无 canonical 稳定形态，故迁出而非规范化）。
- 等价性测试更新为"减去且仅减去三条易变行"+ 新增 in-memory vs DB round-trip
  字节等价与时区折叠用例（`tests/narrative/test_narrative_prompt_split.py`）。

## 2026-07-28 — R4a：narrative 模板拆分（稳定半 + turn 半）

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

`build_main_prompt` 新增 `include_volatile: bool = True`：True = 完整旧模板
（relocation 开关关时的字节等价路径）；False = 稳定版模板（id/type/created_at/
name/description/actors——CLI session 生命周期内恒定，narrative 切换 = 新
session），进 system prompt 可缓存前缀。新增 `build_turn_prompt(narrative)`：
渲染两个每轮易变字段（updated_at 每轮变、current_summary 每轮 LLM 重生成）为
`## Current narrative state` 块，进当前轮消息的 [Turn context]。actor 显示名
解析留在稳定半——display name 变更属合法打穿。拆分硬标准（稳定版 = 旧模板渲染
减去且仅减去两条易变行）由 `tests/narrative/test_narrative_prompt_split.py` 锁定;
改共享文案必须同步改 [[prompts.py]] 的两个模板。

## 2026-06-12 — actors rendered by HUMAN name (user_id is opaque in cloud mode)

`build_main_prompt` now resolves USER / PARTICIPANT actors to their human
display_name before rendering the actor list. Their `actor.id` is a `user_id`,
which in cloud mode is an opaque NetMind userSystemCode (32-hex) — showing it to
the LLM as a person is wrong. AGENT / SYSTEM actor ids are agent_id / system
keys and stay verbatim. Resolution goes through
[[user_repository.py]] `UserRepository.get_display_name(actor.id)` (the single
DRY id→name resolver), reached via `get_db_client()`. `get_display_name` falls
back to the id when there is no display_name / no such user, so nothing breaks
when an actor is unknown. Part of the Phase-1 user_name/user_id separation —
see [[basic_info_module.py]] for the canonical identity injection.

# prompt_builder.py — Narrative prompt assembly

## 为什么存在

`PromptBuilder` 把一个 `Narrative` 对象转换成给 Agent 推理用的结构化 system
prompt（main prompt）以及 summary prompt。它是 narrative 子系统对外暴露提示词
形态的唯一出口。

## 上下游关系

**依赖谁：** `..models`（Narrative / NarrativeType / NarrativeActorType）、
`.prompts`（各 type / actor 描述常量 + `NARRATIVE_MAIN_PROMPT_TEMPLATE`）、
以及 [[user_repository.py]]（actor 人名解析，运行时通过
`xyz_agent_context.utils.db.db_factory.get_db_client` 取 DB）。

**被谁用：** narrative 的 prompt 组装路径。`build_main_prompt` 是 async，因为
actor 人名解析需要查 DB。
