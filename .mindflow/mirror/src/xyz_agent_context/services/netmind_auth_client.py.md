---
code_file: src/xyz_agent_context/services/netmind_auth_client.py
last_verified: 2026-06-11
stub: false
---

## 2026-06-11 — 5xx carrying {success:false} maps to 401 not 502

verify_token now best-effort parses the body BEFORE the status-code fallbacks: a {success:false} envelope is treated as a rejected token (NetmindAuthError -> 401) even on a 5xx. Observed in testing: a non-NetMind JWT yields a 500 wrapping NetMind's auth rejection — that's the caller's bad token (401), not an upstream outage (502). A genuine 5xx with no parseable envelope still maps to 502.

## 2026-06-11 — userSystemCode field name confirmed against dev

Live-probed NetMind dev (auth-api at userauth.protago-dev.com) with a real test account: emailLogin (DES-CBC + ckType=2, no reCAPTCHA) and /user/balance both 200; response shape is {data:{user:{...userSystemCode...}}} exactly as assumed; the user object does carry loginToken/nettyToken/salt/loginPassword, so the secret-stripping before storing `raw` is load-bearing, not paranoia. The snake_case fallback for the field name is now defensive-only.

# netmind_auth_client.py — NetMind 账号体系的 token 校验客户端

## 为什么存在

NarraNexus cloud 模式的登录已切换为 NetMind 账号体系（Phase 1 用户系统统一，spec 见 deploy 仓库 reference/auth/specs/phase1-user-system-unification.md）。NetMind 的 JWT **无法离线验签**——签名密钥包含存在 NetMind 数据库里的 per-user `loginToken`（密码登录会轮换）。第三方服务验证 token 的唯一可靠方式是拿着它调一个 NetMind 的登录态接口。本模块照搬 Arena 的做法：调 `POST /user/balance`，响应携带 user 对象即视为 token 有效。

## 上下游关系

**被谁用**：`backend/routes/auth.py` 的 `POST /api/auth/netmind-login`（登录瞬间调用一次，换发自家 JWT 后业务请求不再依赖本模块——这是"护照换签证"设计，避免 NetMind 可用性成为每个请求的依赖）。

**调用谁**：NetMind auth API（`NETMIND_AUTH_API_URL` env：prod `auth-api.netmind.ai`，test `userauth.protago-dev.com`），httpx 异步请求，5s 默认超时。

## 设计决策

- **协议怪癖**（与 Arena netmind.ts 一致，实测验证过）：认证 header 名是字面量 `token`（值 `Bearer <jwt>`），不是标准 Authorization。
- **错误二分**：`NetmindAuthError`（token 坏了 → 路由层映射 401）vs `NetmindUpstreamError`（NetMind 不可达/契约漂移 → 502）。响应缺 email/userSystemCode 字段算 **upstream** 错误而非 auth 错误——NetMind 改字段名不能表现为"用户登录失败"。
- **dev-bypass 双开关**：`NETMIND_DEV_BYPASS=1` 环境变量 **且** token 有 `dev-bypass-` 前缀才放行（合成确定性身份 `devbp_<sha1(email)[:24]>`），缺一不可；prod 永不设该 env。供 smoke test 的 make_user fixture 使用，免去真实 NetMind 依赖。
- **敏感字段防泄漏**：`/user/balance` 响应的 user 对象携带 loginToken/nettyToken 等敏感字段（NetMind 的 UserRender 不过滤），`raw` 字段存入前剥离这些 key，错误信息里也绝不打印 user 对象本体。
- 字段名 `userSystemCode` 带 `user_system_code` 兜底解析——确切字段名待 test 环境实测（spec 开放问题），兜底避免一次契约确认延误联调。
