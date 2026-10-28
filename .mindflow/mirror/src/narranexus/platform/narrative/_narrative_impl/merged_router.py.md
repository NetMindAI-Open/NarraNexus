---
code_file: src/narranexus/platform/narrative/_narrative_impl/merged_router.py
last_verified: 2026-08-27
stub: false
---

# merged_router.py — 一次调用同时回答"续不续"和"去哪儿"

## 2026-08-27(round 2)— pick_menu 迁出 + input_chars 计全量

`pick_menu` 是纯规则(排序/去重/零分过滤),搬去 [[routing_gate]] 与快门
同住——它住在 LLM 模块里曾迫使 retrieval 反向 import 决策层(round 2 I6)。
`MergedRoutingPrompt.input_chars` 从只计 user_input 改为
instructions+user_input:指令随 2×2 变体变化数百字符,而变体与被测轮型
相关——漏计是斜率系数上的偏差,不是常数偏移(I2);merged_truncated 仍只
覆盖 user_input 侧(指令不受读侧预算),口径差已注明。

## 2026-08-27 — `allowed_verdicts`:本轮供什么,契约就收什么(review Critical 1)

`_contract_violation` 原来单独硬编码"锚点不可续时拒 continue_anchor",而
指令字面量永远把 continue_anchor 列为默认——两边必然打架,模型照 prompt 答
就落 merged_fallback_new(D19 形状)。现在 `allowed_verdicts(inp)` 是唯一
推导:`build_merged_instructions` 渲染的答案表 = 它,契约拒收的补集 = 它。
"哪些 verdict 合法"原来散在 4 处(prompt 散文/VALID_VERDICTS/
_contract_violation/_select_merged 分支),现在散文与契约同源,VALID_VERDICTS
只剩"是不是我们认识的词"这一层。`MergedRoutingOutput.verdict` 的 Field
description 改为中性("one of the verdicts offered")——structured-output
schema 也是 prompt 面,列全五个词等于第五份邀请函。

## 为什么存在

老路是两次串行 helper 调用:continuity 问"这一轮还属于原来那条线吗",判否
之后 judge 再问"那它属于哪条线"。prod 7 天真人轮 n=189 里,43 轮两次都付,
串行 p50 8,924ms;而**整个非 LLM 检索层均值 47.6ms**。也就是说这条路上唯一
有分量的杠杆是**往返次数**,而两个问题的输入几乎完全重叠 —— 这就是合并的
全部理由(spec `2026-08-25-merged-routing-design.md` §4)。

**别再去"修检索的 6 秒"**:那个前提是嵌套计时列被相加两遍(`retrieve_ms`
包含 `judge_ms`),已由设计轮 146/146 行实测推翻。工单在
`todo/2026-08-25-retrieve-ms-nests-judge-ms.md`。

## 这个文件负责什么,不负责什么

**负责**:拼一份 prompt、发一次调用、解析一个答案、判定答案能不能用。
**不负责**:轮次最终落在哪条线上 —— 那是 `narrative_service._select_merged`
的事,而它用的每一个落点都是**既有执行器**(continuity 落点 /
`assemble_match_landing` / `create_from_query` / `_land_no_topic_turn`)。
本批的形状就是**换决策者,不换执行者**;这个分工别合并。

## 承重结构:锚点无条件注入(§3.2)

老路上 continuity 判 yes 就在检索层之前 return:池不加载、菜单不构造,
外来线**没有物理途径**进入任何 prompt。这是一层从没被命名的防线,而 B-7 p07
劫持标本正是被它接住的(第 2–12 轮 `pool=0`,BM25 从没跑)。合并把它拆了。

补偿只能是结构性的,实测数字逼出来的:
- 续接轮里锚点**不在** BM25 top-3 的比例 **26.2%–71.6%**
- 菜单首位是外来线 **26.4%–93.8%**(其中 78–97% 是种子干扰线)
- 锚点本轮拿 **0 分** 的比例 **8.2%–49.3%**(同一条线的连续两轮零词面重叠是常态)

所以锚点**无条件渲染、独立分区、默认答案、与菜单去重**,它在不在 prompt 里
**不是它分数的函数**。`test_merged_routing_prompt.py` 的 rule 1 就是钉这件事;
哪天有人把它改成条件渲染,那边先红。

## 设计决策

**`continue_anchor` 与 `match` 是两个词,不是一个 `matched_index`。**
judge 的 `search` 出口没有"这就是你已经在的那条线"这个概念,于是"确认续接"
与"恰好从菜单里选中了锚点"在审计里无法区分 —— 而只有前者**不该**被记成一次
换线判决。下游语义不同,出口就必须不同。

**菜单不显示 `similarity_score`。** 它是 `raw/(raw+1)`,IDF 在本池现算,
跨池无意义 —— 与"快门不许读总分"是同一条纪律的两个落点。真正可迁移的是
**哪些词命中了**,那才是 rank_pool 一路带过来的东西。

**失败不是答案(rule 6)。** provider 异常、超时、verdict 不在契约内、
index 越界,全部返回 `ok=False`,由调用方兜底把轮次留在原处。把失败读成
"新话题"就是 D19 那两记实锤的形状:新建的线成为锚点,updater 逐轮改写它的
身份直到词法证据"变对"。所以 `decide` 永不抛、永不猜。

**输入预算全在读侧。** 五个上限(上一轮回复 / 锚点 summary / awareness /
本轮消息 / participant 条数)只裁剪 prompt 看到的东西,不改任何存储字段,
且**保头** —— 追问的指代物("讲第一个")在回复的开头。被裁的段落名会写进
`narrative_routing_audit.merged_truncated`:静默变短的 prompt 事后没人能解释。

## 上下游关系

- **被谁用**: `merged_select.select_merged`(唯一调用方;service 只持薄委托)
- **依赖谁**: `routing_blocks`(四个共享渲染块)、`prompts_merged.build_merged_instructions`
(按轮拼装,与 allowed_verdicts 同源)、`prompts._NO_DURABLE_TOPIC_RUBRIC`(经 prompts_merged)。
- **测试**: `tests/narrative/test_merged_routing_prompt.py`(prompt 契约 +
  预算 + 双变体配对)、`tests/narrative/test_merged_routing.py`(流程 / 落点 /
  兜底)

## 坑

`get_helper_sdk` 在模块层导入,测试靠 monkeypatch 这个名字截住网络边界 —— 
挪成函数内 import 会让所有流程测试变成真机调用(并开始烧钱)。
