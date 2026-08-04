---
code_file: frontend/src/lib/mock/fixtures.ts
last_verified: 2026-07-30
stub: false
---

# mock/fixtures.ts — demo / 离线模式的静态数据集

## 为什么存在

[[index]](mock/index.ts)的桩方法返回的数据全部来自这里:demo 部署和无后端
本地开发时,UI 全链路渲染所依赖的 agents / jobs / cost / awareness 等假数据。
契约与 index.ts 相同:**类型必须跟 [[api]] 的响应类型同步**——types/api.ts
的接口加了必填字段,这里的 fixture 不补就会 tsc 失败(这也是本文件的
类型安全网:fixture 是前端类型的第一个"消费者")。

## 变更史

- 2026-07-30 — `mockCostSummary` 补 `cache_read_tokens` /
  `cache_creation_tokens`(total / by_model / daily 三层),跟随 CostSummary
  加缓存桶字段。数值按真实形态配比:缓存读远大于未缓存输入,demo 里
  popover 的"含缓存读/写"行才可见。

## 坑

daily 数据用 `Math.random()` 生成,断言 fixture 数值的测试不要指望它稳定。
本文件 2026-07-30 前没有 mirror,变更史从当日起记。
