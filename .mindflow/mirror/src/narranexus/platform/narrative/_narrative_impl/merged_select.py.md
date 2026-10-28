---
code_file: src/narranexus/platform/narrative/_narrative_impl/merged_select.py
last_verified: 2026-08-27
stub: false
---

# merged_select.py — 合并路由路径的编排(从 service 层搬入 impl)

## 2026-08-27(round 9)— 锚点不得同时坐在 participant 段(I1)

participant 落点的下一轮,落点线既是锚点又在 participant 列表——
不滤则同一条线渲染两段、两个 verdict 都指向它,"留下"被审计成"切线"
(污染 flag 唯一要读的列)。修:渲染/索引用的 `participant_pool` 滤掉
锚点;**prep.participant_narratives 本体不动**(evaluate_bypass 的
participant_present 规则读它,滤本体是行为回归)。反事实侧同步:
anchor_in_menu 的排除集不再把锚点当 participant 排掉(否则该人群恒
False,§3.2 探针系统性说谎且不可回补)。钉:
test_an_anchor_that_is_also_a_participant_is_not_on_the_ballot_twice。

## 2026-08-27(round 7)— 收显式协作者,不再收整个 service(I1)

`select_merged(service)` 是同批三个 impl 模块里唯一反向拿调用方的——
merged_prep 收 retrieval、landings 收 crud,包内先例(default_narratives
文件头)也明说这样避免与 service 的环。签名改为
`select_merged(crud, retrieval, write_audit, *, ...)`;`_land` 收
`(crud, retrieval)`,`_land_failure` 收 `retrieval`;TYPE_CHECKING 不再
import NarrativeService。收益:可脱离完整 service 单测,且 service 新增
私有状态不再自动暴露给编排层。死参数 menu_results 移除(round 3 I2 的
结论"落点池不是选票"不再留反证线索);landing_pool 补类型标注。

## 2026-08-27(round 6)— 观测面补齐 + 直调落点

best_score / scores 照两调用路径同样填充(minor 1:"下游无感"包括
开发者叙事面板);no_topic 落点直调 `landings.land_no_topic`
(minor 3,不再绕 service 委托);participant 渲染器不再二次截断
(minor 2:入口截断是唯一一刀,不变式从调用点约定升级为结构)。

## 2026-08-27(round 3)— landing 池与选票解耦;Landing 迁往 landings

**I2**:match 落地曾复用 menu_results 当尾随上下文池——MERGED_MENU_SIZE
是 env 旋钮,调成 1 会让 ChatModule 拿到的线从 3 条静默缩到 1 条,灰度期
的"回答质量下降"会被错归因给 router。现在落地池 = 全量排名剔锚点
(landing 深度只听 MAX_NARRATIVES_IN_CONTEXT),选票(渲染+契约)仍由
MERGED_MENU_SIZE 管。钉:test_the_menu_knob_does_not_shrink_the_match_landing。
`Landing` 与四个共享 executor 移居 [[landings]](M4:flag-off 路径不再
为一个 6 字段 dataclass import 合并模块的 helper-SDK 链)。

## 2026-08-27(round 2)— participant 入口截断 + prep 迁至 merged_prep

**I1**:契约按 `inp.participants` 校验索引,渲染只显示前 8 条——幻觉出的
`participant #11` 会落到从未上票的线且审计无从区分。现在入口先切前缀
(顺序=P0-4 优先级,只许前缀切片),**进 input 的 = prompt 显示的**;
截断标记由本文件补进 merged_truncated(渲染器不再见到被切的尾巴)。
钉子:`test_rule6_an_index_into_the_unrendered_participant_tail_is_refused`。
BM25 准备段改调 [[merged_prep]] 的模块函数(round 2 I6);LLM 计时打标改走
`agent_framework.llm.call_tagging.tag_last_llm_call`(I4,三处拷贝收敛)。

## 为什么存在

review 2026-08-27 Important 2:narrative_service.py 越过 800 行上限后又
并排放进第二个 255 行 decider,且 5 个 verdict 分支各自手写 6 个松散局部
变量,靠人眼保证赋齐。本文件是那次搬迁的落点:编排进 `_*_impl/`(本仓
分层约定),service 只留薄委托 `_select_merged`。

## 结构

- `select_merged(crud, retrieval, write_audit, *, ...)` 主编排(round 7 起
  收**三个显式协作者**,不再收 service 实例):BM25 prepare(经
  [[merged_prep]])→ 快门或一次 LLM → `_land(crud, retrieval, ...)` 按
  verdict 分发 → `anchor_rules.advance_session_anchor` → `write_audit`。
  service 侧只剩同名薄委托,传 `self._crud/_retrieval/_write_audit`。
- 五个 verdict 的落点全部经 [[landings]] 的执行器(`Landing` 值对象也
  住在那里):`assemble_match_landing` / `load_participant_landing` /
  `land_no_topic`(直调模块函数,不绕 service)/ `create_from_query`
  (retrieval 方法)。**决策语义与最初搬迁时逐字一致**。
- `_land_failure`:RULE 6(失败不是判决)单独成函,prompt 拼装失败与
  provider 失败同归此处(日志文案区分两者)。

## 坑

- `is_reusable_anchor` / `minutes_since` / `advance_session_anchor` 从
  [[anchor_rules]] 导入——**必须保持唯一定义**(2026-08-21 review
  Important 3 的产物),别在这里长出第二份。
- participant 的入口截断(前缀切片,P0-4 顺序)是**唯一一刀**:进
  MergedRoutingInput 的 = prompt 渲染的 = 契约校验的;渲染器已无 cap
  形参(round 8 I1),别把它加回去。
- 落点必须构造**完整的 Landing**——六字段一次给齐,漏一个在构造点炸,
  这正是当初拆出本文件的理由之一。
- best_score / scores 与两调用臂**同样留空**(select() 在自己的出口就
  丢弃它们;round 6 审查断言相反,round 8 实核纠正)——面板分数标签在
  两臂都不出现,改任何一侧前先看 test_downstream_cannot_tell_who_decided
  的钉子。
