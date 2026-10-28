---
code_file: frontend/src/lib/__tests__/onboardDefaults.test.ts
last_verified: 2026-10-16
stub: false
---

## 2026-10-16 (PR#413 round 2) — 改测返回值与失败可见性

测试改为断言返回 `{ok, defaults}`;请求 reject / `success:false` / 字段缺失·null·数组·字符串 都 `console.warn` 并 `ok:false`、绝不 reject;畸形条目被丢弃。
旧的"填充全局表"用例已删除。

# onboardDefaults.test.ts — 服务端默认表 → 前端推荐表

mock `api.getProviderCatalog`:响应填充表(netmind agent/helper 选 V4.1-Flash 而非列表首个 omni)、请求失败不抛且回落列表首个。使用该 api 的组件测试在各自 mock 里补了 `getProviderCatalog`。
