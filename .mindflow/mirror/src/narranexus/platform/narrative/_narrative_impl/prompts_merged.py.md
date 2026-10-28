---
code_file: src/narranexus/platform/narrative/_narrative_impl/prompts_merged.py
last_verified: 2026-08-27
stub: false
---

# prompts_merged.py — 合并路由指令的片段与 per-turn 组装器

## 2026-08-27(round 6)— 第三根拼装轴 with_menu(I3)

空菜单的轮上答案表仍列 match(给不出 index,契约必拒)——round 2 Critical
的同类第三扇门。`with_menu` 进入同一次推导:答案表/优先级/输出格式与
`allowed_verdicts` 一起按 `bool(inp.menu)` 收放;菜单**标题**的空注仍渲染
(字节钉),只有"答案"消失。变体测试 4→8 组合。

## 为什么存在

review 2026-08-27 round 3 I3:合并段在 prompts.py 里长到 ~220 行。
`build_merged_instructions(anchor_is_continuable, with_participants)` 按轮
拼装答案表/优先级/输出格式,与 `merged_router.allowed_verdicts` 同源
(prompt 说有的 = 契约收的,round 1-2 两个 Critical 的最终形态);核心
拆三块:锚点无关共享核心、`_MERGED_CORE_WITH_ANCHOR`(不对称性+上一轮
判据,仅可续时渲染)、`_MERGED_CORE_WITHOUT_ANCHOR`(对称说明+省略式
读法)。

## 坑

- `_NO_DURABLE_TOPIC_RUBRIC` 从 [[prompts]] import——judge 也逐字 splice
  它,唯一拷贝是全部意义,别搬。
- 变体永远走拼接,不落字面量;测试对 builder 四组合循环。
