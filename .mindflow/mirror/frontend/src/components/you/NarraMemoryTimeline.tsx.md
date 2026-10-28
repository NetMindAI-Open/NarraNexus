---
code_file: frontend/src/components/you/NarraMemoryTimeline.tsx
last_verified: 2026-08-06
stub: false
---

## 2026-08-06 — 布局数学抽到 narraMemoryLayout.ts（时间轴越界修复）

Base recvoAmUUSjKXs「时间轴显示异常」：inline useMemo 的几何有三条越界路径
（新近创建的窄条 left≈100% 再加 min-width 戳出「now」、时钟偏差把时间戳排到
未来、全解析失败的兜底恰好落在 100%+min-width），且短跨度下 4 个刻度标签是
同一天字符串（轴看起来坏了）。数学抽成 [[narraMemoryLayout]] 纯函数并钉了
单元测试；组件的 useMemo 只剩一行调用。修复语义在那份 md 里。

# NarraMemoryTimeline.tsx — the Narra Memory tab (real data)

## 为什么存在

The first real-data visualization of the "You" workspace ([[YouWorkspace]]).
Renders the user's lived storylines (narratives across ALL their agents) as a
timeline. Data source: `api.getMyNarratives()` → `GET /api/me/narratives`
([[me]]); seeded scaffold narratives are filtered out server-side.

## 设计决策

- One narrative = one lane: a **carbon** bar spanning `created_at → updated_at`
  on a shared time axis, gutter-labelled by storyline name + a **silicon**
  agent dot (carbon=Narra/you, silicon=the agent that holds it). Point-in-time
  narratives (created == updated, common early on) get a min-width marker so
  they stay visible. Axis = 4 ticks（粒度随刻度间距自适应，见
  [[narraMemoryLayout]]）+ a carbon `now`.
- Click a lane → a detail card (topic_hint, summary, started/last-active,
  rounds, keywords). 几何数学在 [[narraMemoryLayout]]（纯函数、单测钉住）；
  组件的 useMemo 只做一行调用，`search` 原值直接传入（规范化归纯函数管），
  组件自己的 `q` 只服务空态文案。
- Optional `search` prop filters storylines by name / summary / topic / owning
  agent (driven by the [[YouWorkspace]] search box).
- Owner-scoped: it must NEVER read the selected `agentId`.

## 新人易踩的坑

- 时间戳解析、越界 clamp、坏值兜底都在 [[narraMemoryLayout]] 里——别在组件层
  再包一层日期防御，两处防御会漂移。
- When the user has zero lived narratives the component shows an empty state,
  NOT an error — that is the expected cold-start, storylines accrue as agents
  actually converse.
