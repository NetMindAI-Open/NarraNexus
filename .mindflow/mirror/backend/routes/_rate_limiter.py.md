---
code_file: backend/routes/_rate_limiter.py
last_verified: 2026-10-28
stub: false
---

# _rate_limiter.py — 进程内滑动窗口限流器（全仓唯一一份）

## 为什么存在

所有 per-key 限流桶（dashboard agents-status、analytics、渠道匿名 webhook、
auth 的 signup / send-code / netmind-login / funnel-report）共用这一个实现。不要另起第二个限流器。

## 设计决策

- deque 滑动窗口，周期性 cleanup 防止一次性 key 无限增长。
- **拒绝路径不分配 key 是防御性保证，不是封顶**：limit>0 时新 key 总被放行并分配；
  caller-chosen key（email）的 key 空间只能靠调用点把可信的 per-IP 桶放在前面来封顶。
- `retry_after(key)`：被拒后给 429 `Retry-After` 用。被拒 key 的 deque 恰有 limit
  条窗口内记录，最老一条出窗即空出名额；向上取整、最小 1；无状态 key（limit<=0
  退化配置）返回整个窗口。
- `refund(key)`：退还一次 `allow` 给出的名额，用于「先预占、只在成功时保留」的调用点
  （signup 的 per-IP 建成桶只记真建成的账号；同一路由的尝试桶全额计费、不退还，
  否则失败请求没有 per-IP 上限）。选「预占 + 退还」而不是「先查后记」：
  后者在 await 上游期间并发请求会全部通过同一次检查，限额被并发度放大。弹出的是
  **最新**一条——窗口内记录对计数等价，唯一偏差是剩余记录最多早一个在途时长过期。
  无状态 key 为 no-op。
- `env_limit(name, default)`：运维可调限额的共享解析器。未设/空/非整数/<1 一律回默认，
  不在 import 期炸 backend；0 不被接受，因为 `allow` 把 limit<=0 当「全拒」。

## Gotcha

- **触发**：多 worker 部署 → **症状**：实际限额 = 单进程限额 × worker 数 →
  **根因**：状态在进程内存里。需要全局一致限额时得换 Redis 版。
- **触发**：将来开启 IPv6 接入，per-IP 桶直接用完整 IPv6 地址当 key → **症状**：
  一个客户端从自己的 /64 里轮换地址，每次都是新桶，per-IP 限额形同虚设 → **根因**：
  一个终端用户通常就分到一整个 /64。届时 per-IP 的 key 应先归一化到 /64 前缀。
- **触发**：测试里不重置模块级 limiter → **症状**：跨用例 429 → **根因**：限流器是
  模块单例；测试 fixture 用 monkeypatch 换新实例。

测试：`tests/backend/test_auth_funnel_observability.py`、
`tests/backend/test_signup_abuse_limits.py`。
