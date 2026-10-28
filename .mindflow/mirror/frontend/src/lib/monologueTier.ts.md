---
code_file: frontend/src/lib/monologueTier.ts
last_verified: 2026-08-30
stub: false
---

# monologueTier.ts — 一帧 thinking 是不是「独白」

## 为什么存在

NexusPower 的宪法（`constitution.md` 第 1 条）对模型承诺：plain text 是私有
独白，**用户永远不会以消息形式收到它**。但那段文字一直是**显示**给 owner 的
——[[event_adapter]] 把它映射成 `thinking_item` 并盖 `monologue=True`，前端当
普通 thinking 渲染（dim）。

2026-08-30 的设计（代号 A′）把它**在过程语域内**提到「进度」档：更可读，但
**不做成气泡**——不进消息语域，宪法一字不改。这一点靠的是宪法承诺的原话是
"The user never receives it **as a message**"：承诺的是「不是一条对你说的
话」，而不是「看不见」（今天它本来就以 thinking 形态显示着）。

判定这件事需要一个谓词，而这个谓词**两条路径必须共用**（直播
[[chatStore]] / 回放 [[segmentTurn]] 的 `timelineToEvents`），否则刷新前后档位
会不一致——那正是 [[segmentTurn]] 用「同一份实现」立起来的构造性不变量。

## 为什么是**相等**判定，不是 `!!monologue`

这是本文件唯一真正的判断，也是最容易被"优化"掉的一条。

[[response_processor]] 的 `_ThinkingBatcher` 按 ~100ms / 500 字合帧，而且
**只在非 thinking 事件到达时才强制 flush**。独白与 provider CoT 走的是同一条
`thinking_item` 通道，所以**一次窗口内的 monologue→CoT 切换会落进同一帧**：

- `thinking_content` = 并集（按到达序）
- `monologue` = 子集（只把独白拼起来）
- **位置信息不存在** —— 没有任何字段记录子集在并集里的偏移

所以混档帧在这里**不可切分**。判定取"子集 == 并集"（帧是纯的）才算独白：

- 选 `!!monologue`：混档帧会把 **provider CoT 一起提亮**——把我们声明为
  草稿纸的东西推到用户眼前，这是**不能犯**的那一侧。
- 选相等：混档帧回落成普通 thinking，独白在那一帧里混在 CoT 里显示得暗一点
  ——**信息一个字没少**，只是没被提亮，这是**可以承受**的那一侧。

一句话：**宁可漏提亮，不可错提亮。** 后端 [[chat_history_timeline]] 的
`is_monologue_step` 是同一条规则的服务端副本（回放路径在那里就把档位收敛成
了 bool），两边必须一起改。

## 混档帧已经不再产生（2026-08-30 第二版）

上面那条相等判定当初是**保守兜底**：batcher 会跨档合并，混档帧不可切分，
所以只能整帧回落。那次跨档合并已经取消（[[_thinking_batcher]] 换档即 flush，
起因是真机上一句话被从中间撕开），于是**帧天然 tier 纯净，相等判定从「保守」
变成「精确」**——谓词本身一个字没改，前提变了。

保留相等而不是换成 `!!monologue`：万一哪天又有路径产生混档帧，失败方向仍然是
「漏提亮」而不是「把草稿纸提亮」。

## 历史：第一版把分桶推给了 response_processor（后来做了）

第一版把「让 batcher 按档分桶」列为**刻意不做**的后续项，理由是范围限定在
「前端分档 + 一个只读透传字段」。真机上一句话被从中间撕开之后，TC 批准把它
折进来——2026-08-30 已落地，见 [[_thinking_batcher]]。这条留着是因为它记录了
**为什么当时不做、后来什么证据改变了判断**，不是待办。

## 上下游

- 消费者：[[chatStore]]（直播帧，传 `thinking_content` + `monologue` 子集）、
  [[segmentTurn]] 的 `timelineToEvents` 只消费后端已经收敛好的 bool，不再调
  本谓词。
- 档位落到 `ThinkingEvent.monologue`（[[messages]]），由
  [[TurnTimeline]] 与 [[processShared]] 渲染，并受 [[uiStore]] 的
  `interimNarration` 偏好开关约束。
