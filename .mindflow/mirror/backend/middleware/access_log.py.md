---
code_file: backend/middleware/access_log.py
last_verified: 2026-04-28
stub: false
---

# access_log.py — HTTP access log middleware

## 为什么存在

T6 的产物。改造前 backend 完全没有 HTTP access middleware——请求方
法、路径、状态、耗时、谁打的电话都不在日志里，事后排查只能靠代码里
分散的 `logger.info` 在各 route 里碰巧打过的内容。这中间件统一了入
站观测，并且**把 trace 字段注入到 contextvar**，handler 内任何
`logger.*` 都自动带上 `run_id` / `trigger_id`，可以用 `event_id`
之外的另一个维度 grep 出某次 HTTP 请求的全链路。

## 行为

每个 `/api/*` 请求出口处打一行结构化 INFO：

```
http.access method=POST path=/api/jobs status=201 elapsed_ms=42.3
```

异常路径走 `logger.exception(...)`，stack 被捕获。即便外层 error
handler 把异常吞成 generic 500，这里的 stack 也已经留底。

入口生成 `request_id = req_<uuid8>` 注入到 `bind_event(run_id=...,
trigger_id=f"http:{method}:{path}")`，整个 handler 范围内任何 logger
调用都会带这两个字段。

## 跳过列表

`/health` + `/api/dashboard/active-sessions`（前端 SystemPage 每
3s 轮询一次，会刷屏）。短就是优势，不要拆成配置表——加更多 skip
请直接改 `_SKIP_PATHS` / `_SKIP_PREFIXES` 常量。

## 上下游

- 注册：`backend/main.py` 在 `auth_middleware` **之后**注册——
  FastAPI 中间件 LIFO，所以 access_log 实际上**外层**包住 auth，
  401/402 的响应也能产生一条访问日志。
- 依赖：`xyz_agent_context.utils.logging.bind_event`（trace 上下文
  绑定）和 loguru 全局 `logger`。
- 不依赖任何模块内部细节——纯粹的横切关注点。

## Gotcha

- 中间件在异常时会先 `logger.exception` 再 raise，**外层若再有
  middleware 调用 `logger.exception`，会重复一份 stack**。当前没有
  这种情况；如果未来加，要选一个固定层来记 stack。
- `start = time.monotonic()` 必须在 `bind_event` 进入**之前**取，
  否则 contextvar 进入开销会被算进 elapsed_ms。当前实现是对的。
