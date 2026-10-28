---
code_file: src/narranexus/platform/narrative/_narrative_impl/routing_blocks.py
last_verified: 2026-08-26
stub: false
---

# routing_blocks.py — 路由 prompt 的四个共享渲染块

## 为什么存在

三个 tier 在向同一个模型描述同样的四件东西:**已锚定的那条线、上一轮、
BM25 菜单、participant 线**。历史上每一次"复制而不是共享",复制体都分叉了,
而且**只有一份被修**:

- judge 的 search 分支与 participant 分支曾是同一个决定的两份实现;
  2026-04-15 只有 search 分支迁到了活字段 `narrative_info`,另一份继续用
  创建时冻结的 `topic_hint` 标签(本地 dev 库 84% 为空),最长把一条 72 事件
  的线描述成三个月前的第一句话;
- 两份 judge prompt 就 no_topic 判据**分叉了三次**,最后一次是 PR #361
  review round 2 抓到的。

合并调用是第四个消费者 —— 正是这个模式会重演的时刻。所以块下沉到这里,
合并 prompt 是这些块的**组合**,不是第四份文案。

## 字节相同是硬契约

continuity 与 judge 必须渲染出**与本文件出现之前完全一样**的文本:它们的
prompt 被一堆实测数字钉着(M6 = 20.8%、P1 校准、description 退休干跑),
一个空格的变化就静默作废那些数字。所以合并路径需要的每一处差异都是
**具名参数,老行为做默认值**:

| 参数 | 谁用 | 为什么不同 |
|---|---|---|
| `header` | 合并路径给锚点块换成"staying here is the DEFAULT" | 不对称性铁规要在标题上就说清 |
| `summary_max_chars` | 只有合并路径传 | 锚点块在每一轮合并里都渲染,summary 没有硬上界 |
| `include_bucket_note` | 合并路径传 False | 桶开关与合并开关互斥断言 ⇒ 桶不可能占锚点位,留着是自相矛盾的惰性指令 |
| `include_score` | judge 传 True(钉住),合并传 False | 挤压分跨池无意义,与"快门不读总分"同一条纪律 |
| `absent_note` | 两边各自的"没有锚点"措辞 | 缺失的段落读作"没提",显式的"没有"读作事实 |
| `max_candidates` | 只有合并路径传 | 取**前缀**,顺序就是 P0-4 优先级,不许为了塞进预算重排 |

`test_merged_routing_prompt.py` 里有四条 golden 断言(judge 菜单、judge 空
菜单、continuity 锚点块、continuity 上一轮块)逐字节钉住老渲染。

## 读侧裁剪

`clamp_head` 只裁剪 prompt **看到**的内容,从不碰存储,而且永远保头:追问的
指代物("讲第一个" / "the first one")在 agent 上一轮回复的开头,裁尾会正好
丢掉上一轮存在的理由。被裁一定留标记 `…[truncated]` 并回报段落名 —— 隐形
裁剪会让模型把残句读成一句完整(而虚假)的陈述。

## 上下游关系

- **被谁用**: `continuity.py`(锚点块 + 上一轮块)、`_retrieval_llm.py`
  (菜单 + participant 区)、`merged_router.py`(四个都用)
- **依赖谁**: 只依赖 `models.Narrative` 的字段与 `loguru`。**没有** DB、
  没有 LLM、没有 config —— 预算常量由调用方传入,这样它是纯函数、可 golden 测

## 坑

菜单渲染里那条 `logger.warning`(有分数却没有证据)是从 `_retrieval_llm`
原样搬过来的报警,不是新加的。它对 `raw_score == 0` 的 participant 行**故意
不响** —— 那些行没走过 BM25,不欠证据;误报会让人把报警关掉,然后报警就永远
没了(事故教训 #3)。
