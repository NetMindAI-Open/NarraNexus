---
code_file: src/narranexus/platform/narrative/_narrative_impl/merged_prep.py
last_verified: 2026-08-27
stub: false
---

# merged_prep.py — 合并路径的 BM25 准备段(从 retrieval 迁出)

## 2026-08-27(round 9)— 反事实不再把锚点当 participant 排掉 + 记账走共享块

anchor_in_menu 的排除集改为 participants − {anchor}:锚点自己能不能
上榜正是仪器要问的问题,哪怕它同时是 participant 线(participant 落点
后每一轮都是这个形状)。提交块改调 retrieval.commit_scored_pool。

## 2026-08-27(round 3)— rank_depth 退役

全深度排名成为 `_score_pool` 唯一模式(snippet 只算头部),本文件不再
传 rank_depth;锚点排名/在榜与否读的就是真全量。详见 [[retrieval]] 的
round 3 条目。

## 为什么存在

review 2026-08-27 round 2 I6:retrieval.py 越过 1,400 行,且为反事实菜单
import merged_router(executor 依赖 decider 的反向边)。本文件承接合并
专属的准备逻辑:一次 `_score_pool`、锚点的反事实席位(anchor_bm25_rank /
anchor_raw_score / anchor_in_menu,§3.2 的生产仪器)、audit tier-2 半区
的原地填写(含 gate_short_circuit 的原义沿用,铁律 #6)。

## 结构与坑

- 与 [[merged_select]] 同一协作形状:第一参数是 NarrativeRetrieval 实例,
  类型仅 TYPE_CHECKING 引入,不在 import 期上行依赖。
- 反事实菜单必须走 [[routing_gate]].pick_menu(真实菜单规则:排除
  participant、不排锚点)——用 `scoring[:menu_size]` 切片会被同时命中
  BM25 的 participant 线挤出锚点,探针失真且不可回溯(round 2 minor 6)。
- 排名深度 = `config.NARRATIVE_POOL_LIMIT`(与 load_pool 同一常量,I5)。
- commit block 保持"纯赋值不可 raise"的形态,别往里加会抛的逻辑。
