---
code_file: plugins/builtin.common_tools/src/narranexus_plugins/common_tools_module/_common_tools_impl/date_tool.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — 私有平台模块换成公开门面（批 6c，A2-1）

本文件曾 import `narranexus.platform` 的下划线私有模块。批 6c 在拥有它的包上开出了
具名公开函数（`module_system` 的 caller-identity 解析器 / `marketplace` 的
store·pipeline·secret-box / `agent_framework.llm.prompt_probe_emit` /
`agent_framework.adapters.build_tool_policy_guard`），本文件改用它们。
理由不是命名规范：这些 MCP 工具和 helper 自批 6b 起是**独立的 wheel**，
「包内私有」对它们已经不成立了。`pyproject.toml` 的
`plugins never import a private platform module` 契约（只查直接 import）守住这条线。

## 2026-08-18 (review 修正) — `_parse_date` 吃不下本项目自己渲染的 "now"

**新增一条坑，级别等同「改工具名要同时扫三处」**：这个解析器必须认**本平台自己渲染给
agent 看的字节**，不只是 `fromisoformat` 的方言。

提示词反复让模型「把你看到的那个日期传进来」（`reference` / `compare_dates`），而模型最
可能传的就是它在 prompt 里能直接读到的 token。目前有两种：

```
2026-07-31 00:30 +08:00                              ← 时间线行
2026-08-08 09:00:00 +08:00 (Tuesday, Asia/Shanghai)  ← ground truth now
```

第一种在 Python ≥3.13 下 `fromisoformat` 原生就接受（本项目 `requires-python = ">=3.13"`，
时间与偏移之间的空格不是问题）。**第二种因为尾部 `(Weekday, Zone)` 解析失败**，返回
`bad_reference` / `bad_date`。

后果链条是全静默的：工具报错 → 按本文件自己的论证，模型退回自己算（正是要消灭的那步）
→ temporal_guard 记不到（被拒的参数不是日期断言）→ `service_audit` 显示"没问题"。

修法是 `_TRAILING_ANNOTATION` 剥掉尾部 ` (…)`，**刻意只剥这一样**。不能图省事改成"取前
10 个字符"——那会让 `2026-07-30T16:30:00Z` 退回按 UTC 取日期，把跨午夜转换打回原形，等于
换个位置复现本次要修的 bug。`tests/common_tools_module/test_date_tool_round_trip.py`
锁了这条。

真正缺的是**跨组件 round-trip 测试**：渲染器测试和工具测试各自绿，唯一会出问题的组合
（渲染器输出 → 解析器输入）从来没跑过。现在两个渲染器的真实输出直接喂给解析器和两个工具。

# date_tool.py

## 为什么存在

每个 agent 都要处理时间，而在这之前**每个 agent 都是靠推理算日期的**。观察到三类失败，
共同点是全都不报错：

1. 相对表达算错日期。用户周四说「下周五」，agent 把时间戳记对了，但回头提起时说错了是哪天。
2. 存对的绝对日期跟"现在"比错。已经过去的日期仍被描述成即将发生。
3. 跨时区时前两类都会加剧 —— 连"今天"本身是哪天，都取决于读的是谁的钟。

日期说错不是小瑕疵。被告知错误日期的用户，之后对 agent 给的**每一个**日期都会打折。

## 分工：模型做语言，工具做算术

这两个工具**刻意不解析自然语言**。

理解「下周五」是什么意思，是这件事里模型唯一真正擅长的部分。再写一个解析器，就是造了
第二个更差的解释器 —— 依赖语言环境、遇到没预料到的说法就静默出错、而且没法拿真实用法
去测。

所以模型负责拆解（「下周五」→ 本周之后那一周的周五 → `unit='week', offset=1,
weekday='friday'`），工具负责算。各自做自己可靠的那部分，工具的契约因此是**全函数**的：
任意 (unit, offset, weekday, reference) 都恰好返回一个日期，不留歧义。

## 歧义是暴露，不是替用户决定

「next Friday」不同人理解不同（中文「下周五」和「这周五」也不是一回事）。工具无法知道
说话人指哪个，于是**不猜**：只应用一条写明的规则（ISO 周、周一为一周之始），同时在返回里
带上 `week_start` / `week_end`，让 agent 能把落在哪一周摊开给用户看。

用户能看出来错的日期是可以挽回的，光秃秃给一个错日期不行。

## 上下游

**注册在**：`_common_tools_mcp_tools.create_common_tools_mcp_server()`，跟 web_search /
artifact 同一个 MCP server（CommonToolsModule，port 7807）。放 CommonTools 而不是新建
模块，是因为这是所有 agent 都需要的通用能力 —— 铁律 #4，通用逻辑留在通用层。

**时区来源**：`_mcp_identity.caller_user_id_from_request()` → `UserRepository`。走 header
不走参数，理由跟 artifact_tool 一样：`user_id` 是模型填的，模型编一个就会让这个工具返回的
每个日期整体平移那个用户的 UTC 偏移 —— 正好是它存在的目的所要防的那类错误。

**提示词**：`common_tools_module.COMMON_TOOLS_INSTRUCTIONS` 里的 "Dates and Time
Arithmetic" 段；`context_runtime/prompts.USER_TEMPORAL_CONTEXT` 和
`basic_info_module/prompts.py` 的 "Time-bound Commitments" 段也点名了这两个工具名。**改
工具名要同时扫这三处**，否则提示词会指向一个不存在的工具。

## 坑

**`Optional[str]` 不能用。** FastMCP 会把 Optional 渲染成 `anyOf:[X,null]`，严格 schema 的
provider 会在**请求级别**返回 400 —— 整个请求失败，不是这一个工具失败。所以可选参数一律
`str = ""`。这条 artifact_tool 已经付过一次学费。

**weekday snap 在 offset 之后做。** 先位移到目标周，再吸附到那周的星期几。顺序反过来时，
"下周五"在参考日已经过了周五的情况下会多跳一周 —— 一个只在一半日子里出现的 bug。

**月份位移做钳位。** 1月31日 + 1 个月 = 2月28/29日，不是报错也不是 3月3日。钳位才是人说
"下个月"时的意思，也让工具保持全函数。

**错误返回结构化 dict，不抛异常。** 一个会抛异常的工具会教会模型绕开它回去自己算 —— 那
比给一个带明确 frame 的答案糟得多。
