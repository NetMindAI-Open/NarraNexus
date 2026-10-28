---
code_file: src/narranexus/platform/narrative/_narrative_impl/landings.py
last_verified: 2026-08-27
stub: false
---

# landings.py — 所有 decider 共享的 executor + Landing 值对象

## 2026-08-27(round 6)— 菜单行证据懒回填(I2)

`pick_menu` 在 BM25 排序**之后**排除锚点与命中 BM25 的 participant,
所以菜单行可能来自 snippet 头部窗口之外(matched_snippet 空)——词条
在、证据的承重半边缺席,还会误触"wiring is broken"告警(狼来了 =
真告警被静默,事故教训 #3)。`build_menu_candidates` 对
raw_score>0 且无 snippet 的行按 `searchable_text()` 现算,至多
menu_size 次、仅命中该形态的合并轮付费。

## 2026-08-27(round 5)— land_no_topic 归队 + candidate_labels 转正

**I3**:`_land_no_topic_turn` 是落点执行器,四个兄弟都在这里而它留在
service 上,迫使 impl 上行取私有名。搬入为 `land_no_topic(crud, retrieval,
..., anchor=None)`;service 留同签名薄委托(测试与两条路径的调用面不变)。
**陷阱已守住**:可复用性判定(is_reusable_anchor)仍在函数**内部**做,
不信任调用方的 continuable——两条路对桶规则不许分叉;flag-off 侧只取
Landing 四个决定字段的行为不变。`anchor` 参数纯属省一次主键读(M2),
merged 侧传已加载对象、flag-off 传 None 自读。
`_candidate_labels` 更名 `candidate_labels`:它被 retrieval 跨文件消费,
下划线的"不跨文件"承诺已不成立。

## 为什么存在

review 2026-08-27 round 3(I3+M4):四个共享 executor(菜单/participant
候选构建、match/participant 落地)是"decider 换、executor 不换"的本体,
却散在 1,300 行的 retrieval 里;`Landing` 住在 merged_select 又迫使
flag-off 路径为一个 dataclass import 整条 helper-SDK 链。二者同迁于此。

## 坑

- `candidate_labels` 是"候选给模型看什么"的**唯一定义**,随四个消费者
  同迁——绝不许在别处长出第二份(judge 两分支只修一份的历史)。
- 函数第一参数是 NarrativeCRUD(TYPE_CHECKING 引类型),无上行依赖。
- `CANDIDATE_DESC_MAX_CHARS` 随唯一消费者同住。
