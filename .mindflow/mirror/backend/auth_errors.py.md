---
code_file: backend/auth_errors.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-07 — `AuthError` 多继承 `contracts.web.AuthError`（批 6c）

本模块是 backend 的私有模块，插件不许 import 它（新的 import-linter 契约
`plugins never import the host (backend)`）。但 `builtin.auth.netmind` 和
`builtin.teams` 确实需要抛出带 `code` 的鉴权错误。

解法：契约包里放一个纯 Exception 基类（contracts 不依赖 fastapi，只能是纯的），
本模块的 `AuthError` 同时继承它和 `HTTPException`。于是插件
`except AuthError` 成立，而 `install_auth_error_handler` 注册在具体类上、
把 `code` 渲染进响应体的行为完全不变。插件不构造这个类，
它调 `narranexus.sdk.web.auth_error(code, detail)`，由宿主还回自己的实例——
一个 handler 认不出的异常等于把 `code` 字段丢掉，正是本模块存在的理由。

`__init__` 里显式写 `HTTPException.__init__(self, ...)` 而不是 `super()`：
MRO 的第一个是契约基类（纯 Exception），它不收这些关键字。

## 2026-08-13 — `ACCOUNT_SUSPENDED`（403）+ AuthError/response 的 status_code 覆盖

新增 code `ACCOUNT_SUSPENDED = "account_suspended"`，语义是「账户状态不允许交易
（由运维私有设置）」。它以 **HTTP 403** 发出（已认证、但不被许可），**绝不**用
401——JWT 本身完全有效，重登只会铸出同一枚 token 再被弹回。因此它刻意**不进**
`SESSION_DEAD_CODES`：前端应展示一个专属的「账户不可用」状态，而不是登录循环。

为承载这个 403，`AuthError` 和 `auth_error_response()` 都新增了 `status_code`
参数，默认仍是 401（这里绝大多数拒绝都是「未认证」）。仅在「已认证但不被许可」
这一类场景显式传 403：[[routes/auth.py]] 的 netmind-login 对停用账户
`raise AuthError(ACCOUNT_SUSPENDED, ..., status_code=403)`；[[auth]] middleware 的
账户状态闸门用 `auth_error_response(..., status_code=403)`。`install_auth_error_handler`
的渲染也从写死 401 改为透传 `exc.status_code`。body 形状（`{"detail", "code"}`）不变。

## 2026-08-06 (review R2) — 未验签 claims 必须先过 `_render_claim`

`_token_lifetime` 原本把 iat/exp/user_id 直接拼进日志行。问题在于它的两个
调用点之一是 **TOKEN_INVALID**——签名没验过，整个 payload 是调用方随手写
的。两个后果：

- **日志注入（CWE-117）**：`user_id` 里塞一个换行加一条伪造的
  `[auth-reject]` 文本，一次请求就能在审计日志里种出第二行看起来货真价实
  的记录。本模块存在的理由就是"让下一个 8/2 可定性"，而一条谁都能伪造的
  取证日志把这个理由取消掉了。
- **体积放大**：100KB 的 `user_id` = 每个请求一行 100KB 日志，无需认证、
  可重复。

现在每个 claim 走 `_render_claim`：`repr()` 把换行变成字面 `\n`（不再断
行），并截断到 `_MAX_CLAIM_CHARS`。两个用例钉住（伪造行、超长 claim）。

# auth_errors.py — 401 的语义词汇表 + 可观测性

## Why it exists

2026-08-02 线下活动现场，用户反馈"整个页面重新加载了、很混乱"。根因不是
reload（前端全程没有 `window.location` 赋值），而是**任意一个 401 都会拆毁
整个 SPA 会话**：[[api.ts]] 只能看到 HTTP 状态码，于是把下面三类完全不同的
失败当成同一件事——

| 真实含义 | 例子 | 会话是否真的死了 |
|---|---|---|
| 会话 JWT 过期 / 伪造 / 缺失 | middleware 三处出口 | ✅ |
| **另一套**凭证失败 | NetMind loginToken（billing、providers 的 netmind 端点）、Manyfold 网关 token、OpenAI 兼容面的 API key | ❌ |
| 处理器拿不到 middleware 已经验证过的身份 | `resolve_current_user_id` 空 `request.state.user_id` | ❌（我们自己的接线 bug） |

8/2 日志里 16-17 点那 10 个带 token 的 401 中就有 `/api/providers`，正是第二类。

本模块给每个 401 打上 `code`，并规定只有 `SESSION_DEAD_CODES` 才意味着登出。

## Design decisions

**允许清单，不是拒绝清单。** 旧实现是"任何 401 都登出，除非端点是
`/api/auth/login` 或 `/api/billing/` 前缀"——每个漏加的端点都是一颗地雷，
`/api/providers` 就是踩响的那颗。现在未被分类的 `code`（包括前端没见过的
未来新码）一律**不**触发登出：失败方向是"少登出一次"，而不是"多炸一次
会话"。前端侧的同名集合在 [[authFailure.ts]]，两边靠约定同步；某一边缺一个
码只会退化成"不登出"，不会误杀。

**`detail` 保持原样，`code` 纯增量。** FastAPI 默认的 `{"detail": ...}` 形状
不变，人读日志和已有调用方都不受影响。

**middleware 用 `auth_error_response`，路由用 `raise AuthError`。** 中间件在
异常处理链之外，只能直接返回 Response；路由抛 `AuthError`，由
`install_auth_error_handler`（在 [[main]] 注册）渲染成同一个形状。两条路径
共用同一个日志函数。

**`_token_lifetime` 不验签解 token。** 这是纯诊断：读一枚**刚被我们拒绝**的
token 的 iat/exp，是区分"7 天自然过期"和"签名密钥不匹配"的唯一手段——后者
表现为 `exp` 还在未来却报 `token_invalid`（例如一次轮换了 JWT_SECRET 的重新
部署）。8/2 留下的"这 10 个 401 到底为什么"就是因为没有这一行而无解。绝不能
用它做任何授权判断。

## Upstream / Downstream

- **写入方**：[[auth]]（middleware 三处 + `resolve_current_user_id`）、
  [[websocket]]（WS AuthError 帧的 `error_code`）、以及所有路由级 401
  （[[billing]] / [[providers]] / [[notifications]] / [[quota]] /
  marketplace_teams / admin/quota / openai_compat / manyfold/*）。
- **消费方**：前端 [[authFailure.ts]] → [[sessionGuard.ts]]。
- **测试**：`tests/backend/test_auth_401_codes.py`。

## Gotchas

新增一类 401 时，**必须**同时决定它属不属于 `SESSION_DEAD_CODES`。忘记加
`code` 的后果是安全的（不登出），忘记把真·会话死亡加进集合的后果是用户
卡在红色报错里没有重登入口——这正是 2026-05-27 修 WS AuthError 时的原始
症状。
