---
code_file: backend/routes/_rate_limiter.py
last_verified: 2026-04-13
stub: false
---

# backend/routes/_rate_limiter.py — Intent

## 为什么存在
Dashboard 端点每 3s polling，合法用户流量约 0.33 req/s。但恶意用户开 100 个 tab 就是 33 req/s，每个请求扇出 4+ DB 查询——需要一个**最低限度**的 per-viewer 限流（security critic rev-1 M-5）。

不上 `slowapi` / Redis——单进程 in-memory sliding window 对单 worker 部署刚好够。

## 上下游
- **上游**：`backend/routes/dashboard/routes.py` 的主路由和每个 lazy 详情路由（job/session/sparkline/retry/pause/resume）都在入口调 `SlidingWindowRateLimiter.allow(viewer_id)`
- **下游**：无（纯内存，无依赖）
- **测试**：`tests/backend/test_rate_limiter.py` 4 个单测（limit、window 恢复、key 隔离、idle cleanup）

## 设计决策
1. **deque 而非 list**：`popleft` O(1)、`append` O(1)。列表 `pop(0)` 是 O(n)，在窗口有很多旧时间戳时慢。
2. **滑动窗口**（不是固定窗口或 token bucket）：
   - 固定窗口容易被"窗口边界突发"绕过（恰好跨窗口的两次爆发）
   - Token bucket 实现复杂、需要 refill rate——对我们 2 req/s 的粗糙要求过设计
   - Sliding window：每次请求清理 `< now - window_sec` 的旧条目 + 看 len
3. **Idle cleanup**（v2.1 security NC-2）：每 `cleanup_interval` 次请求扫一次 `_deques` dict，删空 deque 的 key。防止长期运行后 dict 无限增长。
4. **monotonic time 而非 wall clock**：防止系统时间漂移或 NTP 调整扰动窗口。
5. **单实例 per router**：route 模块顶层构造 `_rate_limiter = SlidingWindowRateLimiter(limit=2, window_sec=1.0)`，所有 endpoint 共享一个——每个 viewer 的 budget 跨端点共用，不是每端点各 2 req/s。

## Gotcha
- **进程级内存**：多 worker 部署（`WEB_CONCURRENCY>1`）每个 worker 独立计数——一个 viewer 总限流变成 `2 × N workers`。和 `active_sessions` 一样的单进程假设；`backend/main.py::_warn_if_multi_worker` 会启动时 warn。若真上多 worker 要切 Redis。
- **测试时 window_sec 要 patch 大**：真实 DB 测试每请求可能 >500ms，几个请求就跨窗口了，触发不到 429。`test_dashboard_route.py::test_rate_limit_returns_429_on_burst` 里 monkeypatch `_window=3600.0`。
- `allow()` 返 bool；route 层负责 raise `HTTPException(429, headers={"Retry-After": "1"})`——**必须带 headers 参数**，普通 `HTTPException` + `response.headers["Retry-After"]=...` 在 FastAPI 里不生效（response 对象会被 exception 路径丢弃）。
- `cleanup_interval` 默认 100——意味着前 100 请求都不清理。低流量长时间运行下这个 dict 会累积。可接受但别忘了。
