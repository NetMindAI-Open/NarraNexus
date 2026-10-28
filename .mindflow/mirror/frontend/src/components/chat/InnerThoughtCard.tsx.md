---
code_file: frontend/src/components/chat/InnerThoughtCard.tsx
last_verified: 2026-09-24
---

## 2026-09-24 ui.toolRenderers（第 18 个注册表）

事件日志里的 tool_output 常不带 tool_name：`withOutputNames` 按时间序把最近一次 tool_call 的名字补上
（与 segmentTurn 同一规则），否则工具渲染器认不出它。`ToolOutputRow` 与 TurnTimeline 同样的
注册表 + PluginBoundary + Suspense 回落。

## 2026-08-30 — `EntryRow` 接上独白「进度」档

`EventLogTimelineEntry` 当时有**三个**渲染面：[[TurnTimeline]]、
`processShared` 的 `ProcessEventRows`（已于 2026-08-30 退役），以及本文件的
`EntryRow`（前两个走 `timelineToEvents`，这里自己实现一份转换）。A′ 落地时
前两个接了档位，这里第一版漏了，review 第 4 轮抓到。**今天只剩两个**：
[[TurnTimeline]] 与本文件。

**这一面其实最要紧**：activity 行只在「本轮没有面向用户的回复」时才写，也就是
后台 job / 渠道触发那类 turn —— 那种 turn **通篇都是独白**，而这张卡片是它们
唯一的查看入口。漏掉这里等于把功能价值最高的场景留在了旧观感上，还会让用户
学不会「亮的 = agent 在说话」（三个面里有一个持续反证）。

两个坑：

- `EntryRow` 末尾那个 `return` 是**所有**非 tool / 非 reply 类型的兜底，不只是
  thinking，所以条件必须写 `entry.type === 'thinking' && entry.monologue`。
- `toEntries` 的 legacy 回退（`res.thinking` → 单条）**没有**档位，保持 false
  即可——它确实不知道，不该猜。

偏好在**卡片层**用 [[useNarrationTier]] 解析（顶层组件，与 TurnTimeline 同规格），
不在 `EntryRow` 里订阅。

## 2026-08-28 — chips 行抽走，格式规则并轨

`RunMeta` 里那段 chips（状态 / 时长 / 花费 / token / 模型）连同私有的
`formatDuration` / `formatTokens` / `formatCost` / `StatChip` 一起搬进
[[RunStatChips]]，因为 Conversation 气泡要渲染同一行。本文件只留下
input/output 两个文本块，chips 交给共享组件，`hasRunStats` 判断整块是否折叠。

抽取顺带了结了 [[tokenFormat]] 里挂着的那条 NOTE：本卡片私有的两个格式函数
与共享实现规则不同（M 档 1 位小数、`$0` 而非 `<$0.0001`），共享后以
`lib/tokenFormat` 为准。**这确实改变了本卡片的渲染**——百万级 token 多一位
小数，不到千分之一美分的运行不再显示成读起来像"免费"的 `$0`。现有测试断言
落在 k 档和 `$0.0041`，两套规则一致，故未受影响。

## 2026-08-19 — 输入侧求和改用共享实现

下面 07-30 那条规则（输入侧 = 全价桶 + cache read + cache write，`?? 0` 兜旧响应）
一字未改，只是实现搬到了 [[tokenFormat.ts]] 的 `inputSideTokens`，本卡改为 import。
起因是账户页新增用量区时同一条规则出现了第四份实现 —— 它出过一次事故（见下），
失败方式是**某个屏幕上的数字少一个数量级**，不是崩溃，所以四份各自演化最危险。

**本文件自带的 `formatTokens` 没有一起收**：它的 M 档是一位小数，共享版是两位，
合并会改变本卡渲染与其测试断言。另记 todo，不夹带。

## 2026-07-30 — token chip includes the cache buckets

The in/out token chip's input side is now
`input_tokens + cache_read_tokens + cache_creation_tokens` (`?? 0` — the
cache fields are optional in the type; responses cached by older builds
lack them). `input_tokens` alone is only the full-rate bucket, so a
cache-warm run's chip showed "33 / 19.5k" while the model actually read
~869k. Same-day counterpart of the CostPopover fix; backend fields from
[[agents/chat_history.py]] `_build_event_meta`.

## 2026-07-23 — run meta header (activity card upgrade)

Expanded view now renders `EventLogResponse.meta`: a stat-chip row
(duration / cost / tokens in-out / models, + Failed/Cancelled badge),
an INPUT block (what the agent received — env_context.input, scrollable,
capped server-side) above the loop timeline, and an OUTPUT block
(final_output) below it. Chips render only when their datum exists so
legacy rows degrade to the old view. Collapsed card got line-clamp-2 on
the summary + hover shadow. Backend counterpart:
[[agents/chat_history.py]] `_build_event_meta` (bug "Agent 内心活动显示
优化"). i18n: `chat.inner.meta.*` in all 10 locales.

## 2026-07-03 — per-source colour + name (scannable), icons dropped

Every activity used to render identically ("Message" + one MessageCircle
icon) — a wall of indistinguishable rows. Each working_source now has its own
COLOUR (SOURCE_META) shown as a left accent bar + a coloured dot + the source
name; IM channels use their brand name verbatim (WeChat / Slack / Telegram /
Discord / NarraMessenger), category sources (job / collaboration / skill /
callback) use a localized label, unknown falls back to a generic activity
label. Per-source ICONS were dropped on purpose — lucide has no brand logos,
so colour + name carries the identity honestly. Expand/lazy-load of the
agent-loop steps (getEventLog + timeline/thinking fallback, distinct
loading/error/empty states) is unchanged.

# InnerThoughtCard.tsx — one inner-thought (activity) as an expandable card

Renders a ``message_type=activity`` row in the chat's Inner Thoughts tab. An
activity is written whenever a NON-chat trigger runs the agent and it sent no
user-facing reply (chat_module.py) — those triggers are diverse (scheduled
job, agent-to-agent bus, inbound IM on any of six channels, skill study), so
the card is headed by ``item.workingSource`` (icon + i18n source name via
SOURCE_META) rather than a flat "Background activity" line.

The turn's steps live in the events table and are fetched lazily by
``item.eventId`` via ``api.getEventLog`` (same endpoint + EventLogResponse
shape MessageBubble uses) — only on first expand, cached in a small
``LoadState`` state machine. ``toEntries`` prefers the response ``timeline``
(EventLogTimelineEntry: type thinking/tool_call/tool_output/native_output/
reply, content/tool_name/tool_input/tool_output) and falls back to
(thinking, tool_calls) for old backends. States are distinct: loading /
load-FAILED / genuinely-EMPTY, and there is no expander when the activity has
no event_id. Self-renders the small step list (not TurnTimeline) to stay
self-contained. i18n keys: ``chat.inner.*`` in all 10 locales. Guarded by
__tests__/InnerThoughtCard.test.tsx (7 tests).
