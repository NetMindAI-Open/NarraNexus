---
code_file: src/xyz_agent_context/narrative/_narrative_impl/routing_gate.py
last_verified: 2026-07-29
stub: false
---
# routing_gate.py — 「BM25 够不够格自己拍板」的判据

## 为什么存在

`retrieve_top_k` 里原本是三行内联条件，和 DB 加载、候选拼装缠在一起。它是整个
路由里**唯一需要拿离线评测集调参**的部分，所以拆成纯函数：输入一组原始分，输出
决定 + 理由，可单测、可对拍、可回归。

## 旧判据为什么是坏的（prod 2026-07-29，agent_dd505db5ff12）

`_keyword_search` 把 BM25 原始分挤成 `s/(s+1)`，判据拿它跟 0.70 比 —— 代数上
等价于 **原始分 ≥ 2.33**。中文没空格，`tokenize` 只能出单字 unigram，于是
`工业` 和 `武道具` 共享 `业`、`高铁新城` 和 `高井武道具` 共享 `高`，几个偶然
碰撞就够越线。**273 条真实 prod 轮次实测：旧判据短路 87.5%**，LLM 仲裁层等于
死代码。用户症状是"第二天问 B，agent 答第一天的 A"。

## 为什么是两个条件而不是一个

- **原始分绝对值没有跨 agent 可比性**：`bm25_rank` 的 IDF 是在候选集内部现算
  的（5–12 个 narrative），同样的重合度在不同 agent 上给出不同数值。
- **但同一次查询内部的间距有意义**：所有候选共享同一张 IDF 表。真命中会拉开，
  噪声匹配会挤成一团。

所以 floor 杀"全都弱，挑个最不弱的"，margin 杀"全都还行，随便挑一个"。

## floor 是噪声过滤，不是强度测试（反直觉，别调高）

实测 top1 原始分随 query 长度涨：**<40 字符中位数 5.3，>40 字符 12–15**。所以
高 floor 会系统性毙掉**短追问**（"绘制个图表""给我个下载链接"），而那恰恰是
路由最要紧的场合。第一版设计把 floor 定成 8.0，评测集打脸后降到 3.0，判别工作
全交给 margin。

## margin 为什么是 2.0 而不是 1.5

代价不对称：**误 defer 只是多一次 helper LLM 调用，而且 LLM 大概率确认 top1；
误 accept 会污染整条会话线，还会被 `narrative_service` 的 `continuous` 路径
（复用 session.current_narrative_id，无话题校验）锁定好几轮。**

实测 margin=1.6 能挽回 19/44 误 defer，但会重新放进 3 个真实误路由——不划算。
额外加"绝对差值 gap"规则只能挽回 4/44，收益太小，按 YAGNI 砍掉。

## 坑

- **必须喂原始分**。喂 `similarity_score`（squash 后）会毁掉唯一可比的间距信号。
- 短路失败不是错误、也不是降级——它路由到 `_llm_unified_match`，那是个**严格
  更强**的决策器（同样的候选 + default narrative + 可以判"都不匹配，新建"）。
- 参与者（participant）narrative 带的是合成中性分、从没有过 BM25 分，`raw_score`
  保持 0.0，不能参与判据。
- 回归网在 [[test_routing_gate_regression]]：50 个真实分数形状 + 期望结果。改
  常量必须同步重算期望值，测试会挡住静默漂移。
