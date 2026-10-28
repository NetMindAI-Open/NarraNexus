---
code_file: frontend/src/components/chat/RunStatChips.tsx
last_verified: 2026-08-31
stub: false
---

# RunStatChips.tsx — 一轮运行的统计药丸行

## 为什么存在

两个界面要回答同一个问题「这一轮花了多少」：Inner Thoughts 的运行卡片
（[[InnerThoughtCard]]）和 Conversation 的消息气泡（[[MessageBubble]]）。
2026-08-28 给气泡加单轮用量时，本可以把卡片里那段 chips 复制一份——但
[[tokenFormat]] 存在的全部理由就是这种复制：同一轮在一个屏上读 2.4M、另一个
屏上读 2.40M，读者无从判断哪个是四舍五入过的，而且**它已经真实发生过一次**
（2026-07-30，一周 1.2M token 显示成 "input 213"）。所以抽成组件，而不是复制。

## 上下游

- **被谁用**：[[InnerThoughtCard]]（卡片里 input/output 文本块之上）、
  [[MessageBubble]]（disclosure 之上）
- **依赖谁**：[[tokenFormat]] 的 `inputSideTokens` / `formatTokens` /
  `formatCost`；数据来自 `/event-log` 响应的 `meta`
  （后端 `chat_history._build_event_meta`）

## 设计决策

**格式规则以 `lib/tokenFormat` 为准。** 抽取时两边规则不同，必须裁决：共享库
是 SSOT，卡片的私有副本作废。代价写在文件头注释里——M 档多一位小数，极小额
从 `$0` 变 `<$0.0001`（`$0` 读起来像免费，正是共享库当初要防的）。
`formatDuration` 没有共享孪生体，作为唯一的本地规则留在这里。

**`hasRunStats` 单独导出，两个调用方都必须用。** 组件自己在无数据时返回 null
就够渲染了，但调用方需要**提前**知道，才能决定整块容器是否折叠——组件返回
null 并不能消掉调用方那层带 margin 的 wrapper div。`InnerThoughtCard` 的
`RunMeta` 和 `MessageBubble` 的 chips 落位都走这个谓词。

**谓词 `hasRunStats` 与本文件的渲染是两份手写副本**，不是结构上的保证：加一枚
chip 要**两处都改**，漏改的表现是"只有这一项数据"的轮次整行不渲染，静默且难
查。设计理由（为什么没上 registry 抽象、哪两条已经被抽成共享函数）见
[[runStats]] 的「设计决策」。

**花费 chip 的门在 [[runStats]] 的 `hasCostToShow`（`> 0`，不是 `!= null`）。**
记成 0 的账是"不知道价格"而非"很便宜"，详见该文件的 Gotcha。

## Gotcha

- `inputSideTokens` 而非 `meta.input_tokens`：后者只是全价桶，缓存命中的一轮
  里 cache read/write 占输入侧 99% 以上。
- `meta.models` 直接渲染真实模型 id；这里**不是** `/costs` 那种
  `__main_model__` / `__helper_model__` 合成键，不要套 `shortModelName`。
- **chips 必须留在 disclosure 外面。** 这条被
  `messageBubbleRunStats.test.tsx` 的 "keeps the chips when the turn upgrades
  to segment mode" 一例钉住：timeline 里带 `reply` 时 `segmentTurn` 会切出
  segment，`{segmentsForRender === null && …}` 整块 unmount。挪回去的话，有回
  复的轮次（也就是绝大多数）chips 一闪就没。该用例走 `segmentTurn` 真实实现，
  不要 mock 它——mock 掉就变成测 mock 而不是测行为。
