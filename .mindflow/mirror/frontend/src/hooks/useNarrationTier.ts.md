---
code_file: frontend/src/hooks/useNarrationTier.ts
last_verified: 2026-08-31
stub: false
---

# useNarrationTier — 独白该不该按「进度」档渲染

## 为什么存在

[[uiStore]] 的 `interimNarration` 偏好有**两个**渲染面要读：

- [[TurnTimeline]] —— 唯一的过程渲染器，直播 / 落定 / 团队观察三条路都是它。
- [[InnerThoughtCard]] —— 内心活动页。**最容易漏、也最要紧的一个**：activity
  行只在「本轮没有面向用户的回复」时才写（后台 job、渠道触发），那种 turn
  **通篇都是独白**，而这张卡片是它们唯一的查看入口。漏掉它，用户就学不会
  「亮的 = agent 在说话」——两个面里有一个持续反证。

两份 `useUIStore((s) => s.interimNarration)` 就是两个「改缺省值 / 换来源时
会漏掉一个」的点。收成一个薄 hook——`hooks/` 本来就是这类薄封装的家
（`useReducedMotion` 同规格）。

## 在哪一层解析：顶层组件，不在共享子件

偏好由**顶层组件**读出，再以 prop 传下去：

- [[TurnTimeline]] → `ThinkingBlock` 的 `narration`
- [[InnerThoughtCard]] → `EntryRow` 的 `showNarration`（**无默认值**，漏传由
  tsc 当场抓；第一版写了 `= true`，那把契约从编译期强制降成约定，而且默认
  方向还是反的——忘了传 = 无视用户「关闭」）

**共享子件不许自己伸手进 store。** 一个不在 props 上的隐藏输入，会让下一个
复用它的人遇到「events 传对了、显示还是不对」。这条原则在 2026-08-30 是围绕
已退役的 `ProcessEventRows` 立的，载体换了，原则没变——今天它落在
`ThinkingBlock` / `EntryRow` 这两个 prop 上。

[[TurnTimeline]] 自己调本 hook 是对的——它是顶层渲染器不是共享行组件；
改成从 props 传会让**它自己的调用点**（`MessageBubble` / `SegmentedReply` /
`TeamMemberPanel` —— 这三个是 TurnTimeline 的调用方，**不是本 hook 的**）
都被迫加一个与自己无关的 prop。本 hook 的调用方只有上面那两个。

## 2026-08-31 — 修正：上一版这份 md 描述的是同 commit 删掉的组件

首版把存在理由写成「三个面：TurnTimeline / ProcessPanel / TeamMemberPanel，
后两者再往 `ProcessEventRows` 传 prop」，还专门有一节「为什么
`ProcessEventRows` 不调它」——而**同一个 PR 删除了 `ProcessPanel` 与
`ProcessEventRows`**，并且 `InnerThoughtCard` 这个真实调用方一次都没出现。
一份新建即失真的 Tier-2 文档（PR #378 review 🔴）。

失真的代价具体是：下一个改叙述档偏好的人按文档去找 `ProcessPanel` 会一无所获，
**同时不会知道 `InnerThoughtCard` 也在读这个偏好**——恰好是最不能漏改的那个面。
