---
code_file: frontend/src/lib/utils.ts
last_verified: 2026-08-19
stub: false
---

## 2026-08-19 — 新增 formatAbsolute

绝对日期时间标签(locale 感知,dateStyle medium + timeStyle short),
经 `parseUTCTimestamp` 解析——后端时间戳不带时区标记,裸 `new Date` 会当
本地时间读,悬停绝对时间会和旁边的相对标签对不上。坏输入回空串。
首个消费方:[[../components/settings/ArtifactsSection]] 的时间列 title。

## 2026-08-19 — formatMessageAge 对坏时间戳降级为空串

`Intl.RelativeTimeFormat.format(NaN)` 会抛 RangeError——渲染期一个坏时间戳
等于整块面板进 error boundary。现在不可解析的输入直接返回 `''`(空标签,
不是崩溃)。这个守卫护住全部调用点(MessageBubble / TeamWorkspacePanel /
ArtifactsSection),各处不必再自带 NaN 分支。

## 2026-08-06 — formatMessageAge

新增聊天气泡专用的本地化相对时长(Intl.RelativeTimeFormat,numeric:auto,
秒/分/时/天/月/年阶梯,传 i18n.language)。与既有英文缩写版
formatRelativeTime(jobs/system 面,7 天后落日期)并存,勿混用。

## 2026-08-14 — `activeLocale` 导出

从模块私有改为导出，这样产品里每一个日期格式化都走同一道保护。团队 transcript 的日期分隔线
此前直接用 `i18n.language`：既没有那个 try/catch（Intl 对畸形 tag 抛 RangeError，而分隔线
是在渲染期算的——整块 transcript 掉进 error boundary，不是一个空白时间戳），也没有
`resolvedLanguage`（`zh` 回落到 `zh-CN` 时，分隔线和气泡里的时间会用两种格式）。

**调用它，不要缓存它**：语言切换器是运行时改的，缓存会让格式停在上一个语言直到刷新。

## 2026-05-27 — formatChatTimestamp for IM-style sidebar rows

AgentList previously called `formatTime` (HH:MM:SS) for each row's
last-activity stamp. With no date, a message from three days ago and
one from this morning both rendered as e.g. "14:23:11", confusing
users about how stale the conversation was. `formatChatTimestamp`
adds calendar awareness (WeChat / Lark / Slack convention):

| relative age | rendering |
|--------------|-----------|
| today        | `14:23`     (en-GB 24h time) |
| yesterday    | `Yesterday` |
| 2..6d back   | `Wed`       (en-US short weekday) |
| same year    | `May 18`    (en-US short month + day) |
| cross-year   | `2025/05/18` |

Each branch returns exactly one of {time, weekday, date} so the row is
never ambiguous. `formatTime` itself is unchanged — still used by the
chat bubble / event card surfaces where the date is supplied elsewhere
(day divider, parent header).

# utils.ts — Shared pure utility functions

## Why it exists

Reusable pure functions that have no dependency on React, stores, or the backend. Collected here to prevent copy-paste across components and to give the UTC timestamp parsing fix a single authoritative location.

## Upstream / Downstream

Used broadly: `cn` is imported by nearly every styled component. `generateId` is used by `chatStore` for message and round IDs. `formatTime`, `formatDate`, `formatRelativeTime` are used by chat and job panel components. `truncate` is used in sidebar and card displays.

## Key design decisions

**`parseUTCTimestamp` exists because the backend omits timezone info.** The backend stores and returns timestamps like `"2026-03-11 09:50:09"` — no `Z`, no offset. JavaScript's `Date` constructor treats timezone-naive strings as local time, which means a user in UTC+8 would see times shifted 8 hours early. `parseUTCTimestamp` appends `Z` after normalizing the space separator to `T`, forcing UTC interpretation. Every other time function in this file routes through it.

**`cn` wraps `clsx` + `tailwind-merge`.** The combination allows conditional Tailwind classes (`clsx`) while correctly resolving conflicting class variants (e.g., `p-4 p-2` → `p-2`) via `tailwind-merge`. Using `clsx` alone would accumulate both `p-4` and `p-2` with unpredictable specificity results.

**`generateId` uses `Date.now()` + random string.** Not cryptographically secure, not globally unique across processes. Sufficient for client-side IDs within a single session (chat message IDs, history round IDs). Do not use for anything that needs backend uniqueness.

## Gotchas

**`formatTime` uses `zh-CN` locale.** The `toLocaleTimeString('zh-CN', ...)` call will format as `HH:MM:SS` in 24-hour format, which is the intended design. Users in locales that default to 12-hour format still see 24-hour times. If the UX needs locale-aware formatting, this would need a change.

**`formatRelativeTime` for very old dates falls back to `formatDate`.** Anything older than 7 days shows the full date string. The threshold is hardcoded — there is no configuration option.

## 2026-08-12 — 时间戳跟随用户选择的语言

`formatTime` / `formatDate` 此前硬编码 `zh-CN`，而**五个组件在用它们**：把界面设成英文、法文、
日文的用户，看到的仍是中文格式的日期——整个应用都被翻译了，唯独这一处从不经过翻译文件。

语言**每次调用都重新读**，不在模块常量里捕获：语言切换器是运行时改的，
捕获值会让所有时间戳停留在上一种语言直到刷新。

非法或空的语言标记回落到浏览器本地设置而不是抛异常——`Intl` 会拒绝坏标记，
而一个陈旧的偏好设置不该让产品里每个时间戳变空白。
