---
code_file: frontend/src/components/you/narraMemoryLayout.ts
last_verified: 2026-08-06
stub: false
---

# narraMemoryLayout.ts — Narra Memory 时间轴的纯布局数学

## 为什么存在

[[NarraMemoryTimeline]] 的 lane/tick 几何原本 inline 在 useMemo 里，不可测；
Base recvoAmUUSjKXs「时间轴显示异常」定位到三条画出轴外的路径后抽成纯函数
（`computeTimelineLayout(items, query, now)`，`now` 可注入供测试）。

## 设计决策

- **条永不越过右边界**：右边界定义为「现在」。三个越界源统一治：
  ① min-width 在 left 定位**之后**才补——现在 left 会为 width 让位
  （`left = min(pct(c), 100 - width)`）；② 快于客户端时钟的时间戳（服务器
  偏差/坏数据）clamp 到 now；③ 全部时间戳解析失败的兜底同样被 clamp 覆盖。
  选择在数学里修而不是 CSS overflow 裁剪：戳出去的条读起来像数据坏了，
  裁掉一半的条同样像数据坏了。
- **刻度标签粒度按「刻度间距」而非总跨度选**（review 修正）：决定标签是否
  撞车的是相邻刻度的间隔 `span/TICK_COUNT`，不是 span 本身——3 天跨度下刻度
  只隔 18h，日粒度必撞；秒级跨度下分钟粒度也撞。三档：间距 <1min 带秒、
  <1天 带时分、否则日粒度。测试钉住 3 天与 90 秒两个此前绕开的失效区间。
- 过滤（search，规范化由本函数负责，调用方传原值）/排序（created_at 升序，
  坏时间戳与定位同用 `?? now` 兜底：排最后且画在右缘，视觉一致）/空集返回
  null 的语义原样保留。

## 新人易踩的坑

- `now` 参数是刻意注入的——组件传 `Date.now()`，测试传固定值；别在函数内
  取当前时间（不可测且 resume 语义差）。
- min-width 让位逻辑意味着 left 可能比 pct(created_at) 小——这是设计
  （视觉在轴内 > 几何精确），别当 bug 修回去。
