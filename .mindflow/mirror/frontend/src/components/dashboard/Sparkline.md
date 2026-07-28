---
code_file: frontend/src/components/dashboard/Sparkline.tsx
last_verified: 2026-04-13
stub: false
---

# Sparkline.tsx — Intent

## 为什么存在
给 agent 一个"过去 24h 忙成什么样"的**趋势感**——单张卡片的 snapshot 没法表达"刚忙完一阵"还是"忙了一整天"。1 根柱/小时 × 24 根，高度按 events/hour 规范化。

## 上下游
- **上游**：`AgentCard.tsx::OwnedCard` expanded 态渲染
- **下游**：`lib/api.ts::getAgentSparkline(agentId, hours=24)` → 后端 `/api/dashboard/agents/{id}/sparkline`
- **后端**：`backend/routes/dashboard/routes.py::agent_sparkline` 端点 → `fetch_sparkline_24h` 做 GROUP BY hour

## 设计决策
1. **懒加载**：不放在主 `/agents-status` 响应里——每个 agent 多 24 个数字会让主 polling 响应膨胀。单独 endpoint，卡片展开时才 fetch。
2. **useEffect 在 mount 时 fetch 一次，不追踪更新**：sparkline 更新频率要求低（小时粒度数据），不随 3s polling refetch。下次展开会重新 mount → 重新 fetch，足够。
3. **高度规范化到最大值**：`h = (v / max) * 22`。保证即使数据 range 小（都是 0-2）也有视觉对比。`max = max(1, ...buckets)` 防止全 0 时除零。
4. **最后一根柱加粗**（opacity 100% vs 60%）：强调"刚刚"的活动，和"过去"区分。
5. **颜色跟随 `agent.health`**：error 态 sparkline 就是红的——视觉一致，不用用户对应两套颜色系统。
6. **Loading 态 skeleton**：24 根高 2px 的淡柱，保持卡片高度稳定不跳动。

## Gotcha
- **数据源陷阱**：后端 `fetch_sparkline_24h` 用 SQLite 的 `strftime('%Y%m%d%H', ...)` 分组——MySQL 不吃这个 format，会静默失败返回空数组。实际要看项目当前 DB backend；如果是 MySQL 需要换 `DATE_FORMAT`。目前路由 try/except 兜底但会静默。
- **时区问题**：`strftime('%Y%m%d%H', created_at)` 在 SQLite 取 **数据库存的原始时间**（本地 vs UTC 看插入时）。如果 events.created_at 是 UTC，柱状图显示的 "12-13h" 是 UTC 的 12-13h，不是用户当地时区。未来要加时区偏移参数。
- **网络失败 silent fallback**：error 态返回 `24h · —` 一小行。不 retry、不通知用户。如果 API 长期挂这个指标会永远消失；可接受因为是 nice-to-have。
- 1 根 3px 宽的柱 × 24 根 = 72px 宽。卡片最窄的断点要能容纳，否则会裁剪。目前 grid `md:grid-cols-2` 下单卡至少 ~400px 够用。
