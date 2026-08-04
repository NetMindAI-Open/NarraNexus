---
code_file: src/xyz_agent_context/utils/model_pricing.py
last_verified: 2026-08-03
stub: false
---

# model_pricing.py — 每 token 美元定价的唯一解析点

## 2026-08-03 — 收成仓库唯一的价格解析器（review #227）

四件事一起改，因为前三件是同一个问题的三面：

**一、裸 `import litellm` 违反铁律 #9。** 初版直接 `import litellm; litellm.model_cost`。
但 [[litellm_client]] 已声明自己是本仓库**唯一**的 litellm 导入点，其 `model_cost_map()`
的 docstring 点名的就是这个失误 —— nexus_power 当年正是这么绕过去的，2026-07-29 的 review
刚填过一次。第二个导入点让「换掉 litellm 只改一个文件」重新变成两个，还跳过了座位里的
`drop_params` / `suppress_debug_info` 静默设置。已改为走座位，并加了一条**静态测试**钉住
（这个失误在运行时完全隐形，只有换客户端那天才暴露）。

**二、与 nexus_power 的 `_price_row` 是两份实现，且 id 解析相反。** 规则逐条一致
（input/output 单价、cache 读写无公布价则回退 input 价、未知返回 None），但那份剥 route
前缀 + 大小写不敏感，本模块两者都不做。后果是**同一个 model id 在两个账本里一个有价一个 $0**。
已抽成一个 `price_for`；`price_usage` 只剩「桶 × 单价」的加法，留在 nexus_power。

**三、统一到宽松那套，不是严格那套** —— 与 review 建议的方向相反，理由是量出来的：账本里
体量最大的 `minimax/minimax-m2.5`（1416 次）在表里的键是 `minimax/MiniMax-M2.5`，**纯大小写
差异**。严格解析把它记成 $0，而单价一直是公布的 —— 那不是保守，是漏，而且不自洽：
`gpt-5` / `text-embedding-3-small` 早就在按厂商价记账。

review 举的 `deepseek-ai/DeepSeek-V4-Flash` 被 `_price_row` 按厂商价记账**不复现**（那个
大小写全表扫用的是完整字符串，匹配不上 `deepseek-v4-flash`）。但现在的 route 剥离**确实会**
让它落到 `deepseek-v4-flash`。这点没有掩盖，而是升级成**模块级已知限制**：解析出的是
「该 model id 的公布价」，聚合器转售价 ≠ 厂商直连价，而 id 形状**无法**区分二者
（`deepseek-ai/…` 与 `anthropic/…` 结构完全相同，裸 `gpt-5` 同样可能走转售）。拒绝剥前缀
保护不了账本，只会让覆盖面变得任意，同时把同样的误差留在其余每一行。`_LOCAL_OVERRIDES`
是已知真实费率时的出口。

**测试策略（review Minor-2）**：解析**规则**的测试跑在一张假表上。原先直接断言真实
litellm 表的内容，会把「上游变了」报成「解析器坏了」—— 上游改一次拼写，或干脆**补上**
`bge-m3`，测试就红而这个文件毫无问题。规则是我们自己的，钉在假表上；真表只留**一条
smoke**，且刻意只断言能力（「座位返回了可用的表，且一个众所周知的 id 能解析出来」）而不是
具体费率。

**四、`warm_cache()`。** 懒加载本身正确（多数进程从不记账），但首次触发点在
`await record_cost(...)` 内部 —— 那 1.5s 的同步 import 落在 event loop 上，会顿住当时所有
并发 WS 帧。backend lifespan 用 `asyncio.to_thread` 预热一次。**不能用 `create_task`**：
代价是同步 import，放进任务里照样阻塞。

## 为什么存在

从 [[cost_tracker]] 里搬出来的。它原本内联着一张手写价目表：

```python
MODEL_PRICING = {
    "gpt-5.1-2025-11-13": {"input": 2.0,  "output": 8.0},
    "gemini-2.5-flash":   {"input": 0.15, "output": 0.60},
}
```

两条。2026-07-30 查线上库时发现，**实际在跑的 7 个 model 一条都没命中** ——
`haiku`(207 行)、`minimax/minimax-m2.5`(1416 行)、
`deepseek-ai/DeepSeek-V4-Flash`(351 行)、`gpt-5`、`gpt-5.4-mini` 全部落空。
后果是 `llm_function` / `llm_stream` / `embedding` 三类共 2254 次调用、374 万
输入 token，`total_cost_usd` 全是 0。`agent_loop` 之所以有钱数，只是因为它走
`sdk_cost_usd` 完全绕开了这张表。

而且**唯一命中的那条本身也是错的**：`gemini-2.5-flash` 写的 0.15/0.60，实价是
0.30/2.50。没人回头维护的两行 dict 不是价目表，是让成本"看起来被度量了"的装饰。

## 数据源选择

改用 `litellm.model_cost`（~2983 条，上游维护，本仓已依赖 —— 见
[[litellm_client]]），关键是它带 prompt cache 三档
(`cache_creation_input_token_cost` / `cache_read_input_token_cost`)，这正是
Anthropic 形状的账单需要的。铁律 #9 的考虑：litellm 只当**数据源**用，整个耦合
封在本文件里，`price_for()` 返回自有的 `ModelPrice` dataclass，换源不影响调用方。

## 职责边界：只做可观测性

按 [[cost_tracker]] 2026-07-28 那条，免费额度的真钱由 LiteLLM 网关在请求路径上
实时计量，本仓不记第二本账。**这里算错或算不出，误导的是看板，不会算错任何人的
钱。** 这正是失败模式定成"返回 None 并大声说出来"而不是"猜一个像样的数"的原因：
编造的价格看起来是对的，因此比一个显眼的 0 更糟。

## 四条设计规则

1. **绝不抛异常** —— 成本记账是可观测性不是流程控制（`warn_missing_usage` 已有
   的同一条规矩）。litellm 形状变化 / 导入失败 / 条目畸形，一律降级 None。
2. **绝不编价格** —— 上游不认识的 model 就是不认识，`_LOCAL_OVERRIDES`
   **故意留空**，是给运维照着账单填的，不是给我们估的。
3. **每个 model 只警告一次**，不是每次调用一次 —— 2254 次调用会产出 2254 条相同
   警告，那只会训练人去过滤日志。而它取代的 `logger.debug` 是另一个极端：静默了
   几个月没人发现。
4. **惰性 + 记忆化导入** —— `import litellm` 要 1.54s。

## 别名归一化

复用 [[model_catalog]] 的 `resolve_cli_alias`，不另起一张表 —— 那张表已经有
`test_alias_targets_are_registered_catalog_models` 守着，家族出新版本时会一起更新；
自己抄一份就是下一个会腐烂的东西。裸 `haiku` 进账本是**正常输入**不是 bug：
`cli_helper._DEFAULT_CLAUDE_HELPER_MODEL` 就是它，用户也可以自己在 slot 里填
（铁律 #15，平台不干涉用户选模型）。

Tests：`tests/utils/test_model_pricing.py`（解析 / 别名 / 未知不猜 / 警告去重 /
cache 分档计价 / 无 cache 定价时回退到 input 价而非 0 / litellm 失效降级）。
