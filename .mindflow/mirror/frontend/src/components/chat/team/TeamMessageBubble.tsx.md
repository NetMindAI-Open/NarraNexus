---
code_file: frontend/src/components/chat/team/TeamMessageBubble.tsx
last_verified: 2026-08-19
stub: false
---

## 2026-08-19 — header 插槽:推理披露上移到气泡顶部

新 `header` prop 渲染在内容之前——与单聊 MessageBubble 的披露位置对齐
(那边是 mb-3 border-b 在正文上方)。footer 仍托 artifact chips 与时间戳。

## 2026-08-15 — 没有 segments 时用共享的空数组

`segments` 在没有边界时原本每次 render 新建一个 `[]`，而它是 `visibleSegments` 那个
`useMemo` 的依赖——于是每次 render 都重算。和 `memberNameMap` 那次是同一个形状：一个看起来
无害的字面量，把三层之外的 memo 打穿。eslint 的 exhaustive-deps 警告说的就是这件事。

## 2026-08-14 (二) — 纯文本那份高亮也走共享的 pattern

用户自己的消息不走 markdown，所以需要一个返回 React 节点的版本，两份实现本身是合理的——
但**规则**只该有一份。`markMentions` 现在用 [[mentionPattern.ts]] 的 `mentionMatcher()` 和
`isAddressed()`，和 AST 那份共用同一个字面量。

## 2026-08-14 — 长消息也要分层；@ 高亮改在 AST 上做

**分层此前对最需要它的消息是关闭的。** 条件写的是 `segments.length && !tooLong`，而
`tooLong` 是 content 的属性、**不随展开变化**——所以超过 500 字的消息折叠时看不到分层，
点了展开还是看不到。而团队 turn 是**唯一**开 `include_monologue` 的路径，带 segments 的
消息就是"思考 + 回答"拼起来的，天然就长。整条后端链路（run_collector → TurnResult →
bus_messages.segments → route → 前端类型）铺出来的收益，被这一个渲染条件吃掉了；能看到
分层的只剩那些本来也不需要分层的短消息。

现在折叠预算**跨 segment 花**：按段累计长度截断，切到一半的那段后面整段丢掉——把回答直接
接在半句思考后面，读起来像 agent 从那半句里得出了结论，是另一条（错的）消息。**不能**用
`shown` 的长度去切 segments 数组：`body` 是整体 trim 的，每个 `s.text` 又各自 trim，两个
字符串没有共享的下标。

**@ 高亮从改写 markdown 源码改成改 AST**（见 [[rehypeMentions.ts]]）。字符串替换不知道
什么是代码块，于是任何包含 `@all` 的代码里会被塞进一段字面量 `<span data-testid=…>`。
markdown 会转义它，所以不是安全问题；是更平庸的那种坏：**用户复制出去的代码是坏的**，
而第一嫌疑人会是模型而不是渲染层。团队房间的主要产出正是代码和命令。

# TeamMessageBubble — 让六方对话读得下去的一条消息

## 为什么从 TeamChatPanel 抽出来

本批改的正是「一条消息长什么样」。**只抽本批要动的**——整体重排作为独立改动更好 review，
混进行为改动里更难 review。抽完面板从 1134 行降到 970 行。

## 四件事，每件都是房间此前不可读或不诚实的一种方式

**身份色。** 见 [[senderIdentity]]。落在气泡左边框**和头像**上——移动端隐藏的是名字，
不是头像，所以身份必须同时活在头像上才满足「移动端同样成立」这条验收。

**长度。** 长报告吃掉整屏。超过阈值默认折叠，阈值与 Inbox 既有的一致，两个面折叠尺寸相同。

**独白 vs 回复。** 只在**服务端记录了边界**时分层（见 [[run_collector]]）。没有 segments 的
消息按整块渲染，也就是它此前的样子——**猜边界比不显示更糟**，猜错就是把深思呈现成结论。

**@高亮。** 只有真实成员和 `@all` 会亮。对 `@` 后的任何词都高亮，会点亮邮箱地址，
并教会读者忽略这个高亮。

## agent 消息的高亮走的是 markdown 预处理

第一版只对用户消息生效——**而 agent @ 另一个 agent 正是交接动作，最需要高亮的恰恰是那一侧**。
因为 `Markdown` 已启用 `rehypeRaw`，改为预处理 markdown 字符串插入 `<span>`。

**插入是构造安全的，不是靠转义**：匹配串是 `@` 加 word/CJK 字符，不可能包含 HTML 特殊字符。
安全姿态也没有变化——模型输出本来就能在那里发 HTML。

## 一处防御是重复的

`segments` 在取值处和渲染处各判一次非空。改其中一处是空操作——我在变异验证时被这一点误导过一次，
差点以为测试没守住。真正的失败模式是**空数组 map 出零个子元素、消息正文凭空消失**，
现在由测试直接钉住。

相关：[[TeamTranscript]]、[[TeamMessageFooter]]、[[senderIdentity]]
