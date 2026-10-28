---
code_file: frontend/src/components/agents/__tests__/AgentActivityCard.test.tsx
last_verified: 2026-08-27
stub: false
---

# AgentActivityCard.test.tsx — 钉住次要区块的条件渲染

## 守的是什么

[[../AgentActivityCard.tsx]] 自己几乎没有逻辑，唯一的真判断是
`hasSessions || hasEvents` 那个门。它值得一个测试，是因为**去掉它不会报错、也不会
让任何现有测试变红**——只会让安静的 agent 在指标下面多出一条光秃秃的空条，纯视觉
退化，靠人眼才能发现。

三个用例：上半永远在（sparkline + 指标）；两者皆空时 `agent-activity-detail`
整块不存在；任一非空则回来。

## Gotcha

四个子组件全部 mock 掉。它们各自有网络请求（Sparkline）和 sessionStorage
（RecentFeed），真实渲染会把这个测试变成它们四个的集成测试，而那不是这里要守的东西。

最初那版断言写的是「统计 `.border-t` 类名出现几次」，已改掉——按 Tailwind 类名计数
会被任何无关的样式调整弄红。现在断言的是容器自己的 `data-testid`，那个 id 就是为
这条断言存在的。
