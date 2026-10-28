---
code_file: frontend/src/lib/authFailure.ts
last_verified: 2026-08-06
stub: false
---

# authFailure.ts — 判断"这个 401 是不是会话死了"

## Why it exists

纯函数层：从 401 响应体里取出后端打的 `code`（见 [[auth_errors]]），只对
`SESSION_DEAD_CODES` 里的码回答 true。抽成独立模块是为了可单测，也为了让
"什么才算会话死亡"这件事只有一个定义点——旧实现把它散在 `request()` 里的
两个 `startsWith` 判断中。

## Design decisions

**允许清单。** 旧逻辑是拒绝清单："任何 401 都登出，除非端点是
`/api/auth/login` 或 `/api/billing/`"。它的默认方向是"炸掉会话"，每个没被
想到的端点都是地雷；`/api/providers` 的 NetMind 401 就是 2026-08-02 踩响的
那颗。改成允许清单后，**没见过的码一律不登出**——包括后端将来新增、这份
前端还不认识的码。

**与后端靠约定同步，不靠共享代码。** 两边各有一份集合。某一边缺一个码只会
退化成"不登出"，这是安全方向。

## Upstream / Downstream

- **调用方**：[[api.ts]]（`request()` 的 401 分支）。
- **对端定义**：`backend/auth_errors.py` 的 `SESSION_DEAD_CODES`。
- **下游**：[[sessionGuard.ts]] 负责在此判断为 true 之后再做一次探针确认。
- **测试**：`frontend/src/lib/__tests__/sessionGuard.test.ts` 第一段。

## Gotchas

`identity_missing`（local 模式没有 X-User-Id）**算**会话死亡：只有重新登录
才能把 `configStore.userId` 填回去。而 `identity_unresolved`（middleware 已
放行、处理器却拿不到身份）**不算**——那是后端接线 bug，把用户踢下线既不能
修好它，还会顺手毁掉现场。
