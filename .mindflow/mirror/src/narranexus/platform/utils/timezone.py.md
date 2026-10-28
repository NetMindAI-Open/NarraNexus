---
code_file: src/narranexus/platform/utils/timezone.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-09 — `format_for_api` 对非 datetime/str 抛 `TypeError`（B-18 根因，复审 I3）

同批把同一守卫落到 `to_user_timezone`（`format_for_api` 不是唯一入口：`format_timestamp_for_agent` /
`format_time_for_user` 走的是这条门），两处异常文案各自具名，追栈时不会指向错的函数；
`tests/utils/test_timezone_format_for_api.py` 对两个入口分别钉住。

B-18 的表象是公告栏路由把整个 `entry.model_dump()` 喂给了 `format_for_api`；根因在这里：
函数的 except 分支把**类型错误**（dict 上取 `.tzinfo` 的 `AttributeError`）当成"格式化失败"，
落回 `str(dt)`——一整个 dict 的 Python repr，被调用方当成合法时间戳原样吐给前端，只有一条
warning 日志说了实话。全仓 38 个调用点里下一个传错类型的，症状还会是"接口 200、字段是一坨
repr"。现在进 try 之前先判 `isinstance(dt, (datetime, str))`，不是就抛
`TypeError("format_for_api expects datetime | str | None, got <type>")`，在调用点炸出来。
**没动的**：`None → None`；SQLite 字符串分支「解析失败原样返回」是设计（见 Gotchas）；
输出格式定宽不变（`backend/routes/auth.py` 依赖它可排序）。测试
`tests/utils/test_timezone_format_for_api.py`。
**调用方契约**（复审 PR#393 M6）：DATETIME 列经 repository 读出只会是 `datetime`（SQLite 路径）或
`str`（MySQL 路径）；新增调用点传 `datetime.date`、int 等别的类型就是 bug，会以 `TypeError` → 500 暴露。

## 2026-08-19（PR#327 审后）— 抽出共享的 `coerce_utc` / `to_datetime6_literal`

两个 admin 端点各自需要把「一个 datetime 或 ISO 字符串」归一化成 UTC：
`warn.py` 的 dedup 读（原 `_as_utc`）与 gateway-key-misuse 的 `hit_at` 幂等锚。两处是同一
套惯例，收敛到这里避免漂移：

- `coerce_utc(value)` —— `None`/`datetime`/ISO 字符串 → tz-aware UTC `datetime`；尾随 `Z`
  按 UTC 处理（`.replace("Z","+00:00")`），naive 当 UTC，不可解析回 `None`（调用方可把
  「解析不了」当「没有」而不 crash）。`warn.py` 用它做 dedup 时间比较。
- `to_datetime6_literal(value)` —— 经 `coerce_utc` 后 `strftime` 成 MySQL `DATETIME(6)`
  字面量 `YYYY-MM-DD HH:MM:SS.ffffff`（UTC），不可解析回 `None`。gateway-key-misuse 端点
  用它把 caller 传的 `hit_at` 在**写入**与**幂等反查**两条路径上归一成同一份字节——一个
  `Z` 形与其偏移形塌到同一字面量，故去重成一行。非法 `hit_at` 返回 `None` → 端点丢弃它、
  事件仍落库（走列默认时间），绝不因非法字面量 500/丢行。

## 2026-08-18 (review 修正) — 把不变量的**边界**写清楚

原来的 section header 写的是「agent 读到的每一个时间都必须从这里渲染」，mirror 里也这么写。
实际只做到了 turn prompt 和从它出发能到的 view 工具。**文档承诺大于代码交付**是比覆盖不全
更糟的问题：下一个往 prompt 里加时间字段的人会以为已经统一了，于是不再想起要过渲染层。

现在 header 里显式分两段列：**覆盖了什么**（ground truth / 时间线行 / recent-actions /
view_narrative / view_event / date 工具）、**没覆盖什么**（`format_for_llm` 的 job 链路、
general_memory、social_network、awareness）。

清单放代码里不放 issue，是因为不变量只有边界可查时才有用——加时间字段的人要能一眼看出
自己在不在圈内。**要么扩清单，要么扩覆盖，不许让两者悄悄漂开。**

`format_for_llm` 的 docstring 也加了说明：**它不属于 agent-facing 渲染层**，尽管名字里有
`_for_llm`。它输出 `2026/8/8 AM 9:00 (Asia/Shanghai)`——有时区名但没有数字偏移、没有星期、
日期形状也不同。名字本身在误导下一个调用者，所以得在 docstring 里挡一道。

## 2026-08-18 — 新增 agent-facing 渲染层

原来这个文件只管两件事：内部存 UTC、对外/对前端转用户时区。这次补上第三件 —— **agent
读到的每一个时间，都必须从这里渲染出去**。

背后的规律是踩出来的：模型没法比较两个时间戳，除非它能看出这两个在**同一个 frame**。
一个值渲染成用户本地、另一个渲染成裸 UTC，在语言模型眼里读不出"这是两个坐标系"，只读得出
"这里有矛盾"，然后它会把矛盾合理化掉。所以这一节的渲染器**一律**带显式 UTC 偏移。

新增：

- `resolve_timezone(tz)` —— 统一的降级点。`users.timezone` 缺失或非法时，时间仍然正确
  （UTC），而且**标签写 "UTC"**，不会把不可用的字符串原样回显给 agent。
- `format_now_for_agent(tz)` —— `2026-04-21 17:45:08 +08:00 (Tuesday, Asia/Shanghai)`。
  从 `basic_info_module` 搬过来的：现在有三个互不相关的消费者需要同样的字节
  （BasicInfo 的 ground truth、date MCP 工具的参考点、[[temporal_guard.py]] 的比对基准），
  留在 Module 里会逼另外两个去 import 一个 Module（铁律 #3）。
- `format_timestamp_for_agent(dt, tz)` —— 渲染**已存储**的时间戳，分钟精度（每行历史都要
  带，秒是噪音）但**保留偏移**。丢掉偏移正是 UTC 存储的聊天历史被当成用户本地墙钟读的
  原因，见 [[context_runtime.py]] 2026-08-18 条目。

`WEEKDAY_NAMES` 也提到这里：星期几标签和日期必须来自同一份计算，两边各写一份迟早会飘。

# timezone.py

Timezone utilities — a consistent layer for UTC storage, user-timezone display, and LLM-friendly time formatting.

## Why it exists

Without a centralized timezone policy, `datetime.now()` calls produce naive datetimes that mix UTC and local time unpredictably across the codebase. SQLite compounds this by returning timestamps as strings. `timezone.py` establishes a single rule: all internal datetimes are UTC (created with `utc_now()`), converted to the user's IANA timezone only at display time (via `to_user_timezone`), and formatted for API responses as ISO 8601 with a `Z` suffix (via `format_for_api`) so JavaScript's `new Date()` parses them correctly.

## Upstream / Downstream

**Consumed by:** `narrative/` (formatting event timestamps for LLM prompts), `backend/routes/` (formatting timestamps in API responses), `module/` implementations that need to express times in the agent's or user's timezone. Re-exported from `utils/__init__.py`.

**Depends on:** stdlib `datetime`, `zoneinfo`. No external libraries.

## Design decisions

**`utc_now()` replaces `datetime.now()`.** Every place in the codebase that needs the current time should call `utc_now()` rather than `datetime.now()`. `utc_now()` returns a timezone-aware UTC datetime, which prevents the common bug of naive datetimes mixing with timezone-aware datetimes in arithmetic.

**`to_user_timezone` handles SQLite string inputs.** SQLite returns datetime columns as strings. Rather than requiring every caller to parse the string first, `to_user_timezone` detects string inputs, strips the trailing `Z` (if any), and calls `datetime.fromisoformat()` before converting. This makes the function safe to call with raw database values.

**`format_for_api` always outputs `Z`-suffixed ISO 8601.** The `Z` suffix is critical for JavaScript interoperability. `new Date("2025-01-15T14:30:00")` (no `Z`) is interpreted as local time in some browsers; `new Date("2025-01-15T14:30:00Z")` is always UTC.

**`format_for_llm` outputs a human-readable format with timezone abbreviation.** LLMs respond better to `"2025/1/15 PM 2:30 (Asia/Shanghai)"` than to ISO 8601. This format is intentionally non-standard because it targets the LLM's language model, not a parser.

**Validation with `is_valid_timezone`.** Timezone strings from user input are validated by attempting to construct a `ZoneInfo` object. Invalid strings produce a descriptive error rather than a runtime `KeyError` later.

## Gotchas

**Naive datetimes are assumed UTC.** Both `to_user_timezone` and `format_for_api` treat naive datetime inputs as UTC by calling `.replace(tzinfo=timezone.utc)`. If a naive datetime was actually created in a local timezone (e.g., by calling `datetime.now()` without UTC), the output will be wrong by the offset of that timezone.

**SQLite timestamp parsing uses `datetime.fromisoformat` which is strict.** Non-ISO strings (e.g., `"Jan 15, 2025"`) in timestamp columns will cause `fromisoformat` to raise `ValueError`, causing `to_user_timezone` to return `None` and `format_for_api` to return `None` or the raw string. The column must contain ISO 8601 values for these functions to work correctly.

**New-contributor trap.** `format_for_llm` returns the string `"Time unknown"` when `dt` is `None` rather than raising an error or returning `None`. Callers that check `if result:` will get truthy behavior for a missing time, which can mask missing data in prompt assembly.
