---
code_file: frontend/src/lib/authHeaders.ts
last_verified: 2026-08-06
stub: false
---

## 2026-08-06 (review R2) — 单点解析

`getAuthHeaders()` 和 `getSessionToken()` 原本各自 `getItem` + `JSON.parse`
同一个 blob、各带一套 try/catch。抽出私有 `readIdentity()`：一处解析、一处
容错。顺带对 token/userId 加了类型判断——persist 里存着别的类型时不会把
`[object Object]` 拼进请求头。

# authHeaders.ts — 出站请求的身份头（唯一来源）

## Why it exists

从 [[api.ts]] 抽出来的，动机很具体：[[sessionGuard.ts]] 要用同样的身份头去
打 `GET /api/auth/session` 探针，但它不能 import API client——
`api → sessionGuard → api` 会成环。抽到这里后两边共用同一份实现，不存在
"探针带的头和真实请求不一样"这种诡异分叉。

`ApiClient.getAuthHeaders()` 保留为薄转发（`lib/download.ts` 等外部调用方
不受影响）。

## Design decisions

**直接读 localStorage，不读 configStore。** 这是 [[api.ts]] 一直背着的老约束
（import store 会成环），抽出来时原样继承。

**两个头同时发。** `Authorization: Bearer <jwt>`（cloud，签名身份）和
`X-User-Id`（local，无签名身份）都发，由后端 `auth_middleware` 决定信哪个
——cloud 只信 JWT（纵深防御），local 只认 X-User-Id。前端保持 mode 无关。

**`getSessionToken()` 单独导出。** [[tokenExpiry.ts]] 的到期预警要拿裸 token
解 `exp`，不需要整组头。

## Upstream / Downstream

- **消费方**：[[api.ts]]、[[sessionGuard.ts]]、[[App]]（经 `getSessionToken`）。
- **写入方**：[[configStore]]（persist 到 `narra-nexus-config`）。

## Gotchas

localStorage 全程 try/catch：隐私模式 / 禁用存储时退化成"无身份头"，请求会
被后端以 `token_missing` / `identity_missing` 拒绝——这是正确行为，不是 bug。
