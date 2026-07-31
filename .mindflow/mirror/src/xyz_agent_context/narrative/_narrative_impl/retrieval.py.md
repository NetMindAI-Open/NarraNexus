---
code_file: src/xyz_agent_context/narrative/_narrative_impl/retrieval.py
last_verified: 2026-07-29
stub: false
---
# _narrative_impl/retrieval.py — 把一句用户输入路由到某条会话线

## 为什么存在

`narrative_service.select()` 只在**连续性检测判定不连续**时才走到这里。本文件
负责回答"这句话属于哪条已有会话线，还是该新建一条"。无向量——embedding 早已
退役，召回是 BM25 + participant 查询 + LLM 仲裁。

## 三层结构

1. **候选召回**：`_keyword_search` 用 [[retrieval.py|memory 的 bm25_rank]] 对
   每条 narrative 的 `name + current_summary + description + topic_keywords`
   打分；`_get_participant_narratives` 另外捞出"用户是参与者"的会话线（关键词
   搜不到它们），以合成中性分 0.5 入池。
2. **判据**：[[routing_gate.py]] 决定 BM25 够不够格自己拍板。够 → 直接返回
   Top-K；不够 → 落到第三层。
3. **LLM 仲裁** `_llm_unified_match`：看候选 + default narrative + participant，
   判"匹配哪条 / 匹配 default / 都不匹配就新建"。**这是唯一的语义检查。**

## 2026-07-29 — 判据从绝对值改成 floor + margin

原来是 `best_score >= 0.70`，比的是 `_keyword_search` squash 后的
`s/(s+1)`——代数上等价于原始分 2.33。中文单字 unigram 下几乎恒真：273 条真实
prod 轮次实测**短路 87.5%**，第三层等于死代码，误路由直接落地并被
`narrative_service` 的 `continuous` 路径锁定多轮。

现在读 `NarrativeSearchResult.raw_score`（新增字段）交给 `evaluate_gate`。
同一批数据短路率 48.0%。定值依据见 [[routing_gate.py]]。

## 坑

- **participant 分支必须绕过短路**（`not has_participant_narratives`）：那些
  narrative 带的是合成中性分，且业务上"别人邀请你进来的任务"应当压过"你自己
  narrative 的高 BM25 分"（P0-4）。改判据时别把这个条件顺手删了。
- 高置信短路返回的是 **Top-K 而不是 Top-1**——第 2、3 条会进
  [[step_4_persist_results.py]] 的辅助 narrative 分支，**每轮复制一份 event
  行**（近 7 天全平台 33.9% 的 event 行是复制品）。这是已知问题，未在本次修复
  范围内。
- `dynamic_summary` 无上限累积，narrative 越大可检索文本越长、越容易赢下任意
  查询——正反馈。同样是已知问题，见诊断报告（本次未修）。
