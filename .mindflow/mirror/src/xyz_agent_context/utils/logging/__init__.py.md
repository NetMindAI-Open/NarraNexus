---
code_file: src/xyz_agent_context/utils/logging/__init__.py
last_verified: 2026-04-28
stub: false
---

# utils/logging — Unified logging infrastructure

## 为什么存在

项目里 142 个文件历史上各自 `from loguru import logger` 裸用，
没有统一格式、没有 trace 字段、有两个互相打架的 helper（`utils/service_logger.py` 给后台进程，
`agent_runtime/logging_service.py` 给 per-run），而 IM trigger 因为 fd 泄漏被迫
关掉 file logging。这个包是 2026-04-28 log 系统改造（feat/20260428-log_system）的基础设施层，
把所有日志能力集中到一个入口，**让进程级 fd 数恒定为 O(1)**。

## 对外 API（只这四个）

| 名字 | 用途 |
|---|---|
| `setup_logging(service_name, *, log_dir, level, fmt)` | **每个进程启动时调一次**。配置 stderr + 滚动 file sink，注册自定义 AUDIT level (no=25)，安装 stdlib `InterceptHandler` 把 uvicorn / httpx / mcp 的 logging 桥到 loguru。同 service_name 第二次调用是 no-op。 |
| `bind_event(**kwargs)` | Context manager，包 `logger.contextualize`。在 AgentRuntime.run()、各 trigger 入口、HTTP middleware 里 `with bind_event(event_id=..., run_id=..., trigger_id=...):` 注入 trace 字段。基于 contextvars，**asyncio task-local**。 |
| `timed(name, *, level, slow_threshold_ms)` | 同时是 `with timed(...)` 上下文和 `@timed(...)` 装饰器（async/sync 自动适配）。出口处发一行 `[TIMED] <name> ok elapsed_ms=...`；异常路径自动 `logger.exception` 后 reraise；超过 `slow_threshold_ms` 自动升级到 WARNING。 |
| `redact(value)` | 脱敏 dict/list/tuple，对 `token / password / api_key / authorization / jwt / secret` 等 key 命中替换成 `***`；JWT 形态的 str 截断成 `xxxxxxxx...`。给 logger.debug 打入参时用。 |

## 设计决策

1. **进程级单 sink，run() 不再 add/remove**——这是根治 fd 泄漏的核心。loguru 的 `enqueue=True` 内部用一对 pipe，per-run add 会让 fd 线性增长。改成进程启动时 add 一次就解决了。代价是失去"per-agent-run 一个文件"的便利，**用 event_id 字段贯穿日志 + grep 替代**。
2. **format 字符串里硬编码 `{extra[run_id]:>8}` 和 `{extra[event_id]:>14}`**——通过 `logger.configure(extra={...})` 给两个字段设默认占位符（`-` 横线），保证未 bind 时也不报 KeyError。
3. **JSON 模式当前用 `serialize=True`**——loguru 默认序列化输出含 process / thread / file 等啰嗦字段，但零配置稳定。将来要更精简的 schema 是 v2 任务。
4. **`InterceptHandler` 抄自 loguru 官方 README**——要保持 frame depth 推算正确（filename != logging.\_\_file\_\_）。同时把 `uvicorn.access`、`httpx`、`httpcore`、`mcp`、`asyncio` 五个 logger 钳到 WARNING，避免 INFO 级别被淹没。
5. **AUDIT level no=25**——介于 INFO (20) 和 WARNING (30)，给安全 / 业务关键事件用（登录、quota、provider 切换、Lark 绑定等）。
6. **`diagnose=False`** 在 file sink 上是硬编码——loguru 的 diagnose=True 会把异常局部变量值打到日志里，**生产泄漏敏感数据**。

## 文件分工

```
__init__.py     # re-export 公共 API
_setup.py       # setup_logging（核心）+ env 解析 + AUDIT level
_context.py     # bind_event（薄壳）
_timing.py      # timed（_Timed 类，同时支持 with / 装饰器 / async）
_redact.py      # redact + 敏感 key 集合
_intercept.py   # InterceptHandler + 噪音 logger 静音名单
```

## 上下游

- **被谁用**：所有进程入口（`backend/main.py`、`module_poller.py`、`module_runner.py`、`job_trigger.py`、`run_lark_trigger.py`、`message_bus_trigger.py`）必须在最早期调用 `setup_logging(name)`；`AgentRuntime.run()` 用 `bind_event` 注入 trace；各 step / LLM SDK / `mcp_executor` 用 `@timed` / `with timed` 测耗时。
- **依赖谁**：仅 `loguru`、Python stdlib `logging` / `inspect` / `os` / `sys` / `pathlib` / `re`。无 NarraNexus 内部依赖（铁律 #3 模块独立）。

## Gotcha

- **`enqueue=True` + 进程崩溃**：队列里未 flush 的日志会丢。各 trigger / FastAPI lifespan shutdown 必须显式 `await logger.complete()`。
- **`logger.remove()` 全局副作用**：`setup_logging` 内部会清掉所有现存 handler。其他代码不要再调 `logger.remove()`，否则会清掉我们的 sink。
- **`logger.configure(extra=...)` 不影响已有 sinks**——只在 add sink 之前调一次即可注入默认 extra。
- **测试时**：测试用 `_setup._reset_for_tests()` 清缓存（私有 API，仅供 tests 用）；fixture 必须 `logger.remove()` 防止 handler 在 case 间堆积。

## 参考

- 设计：`reference/self_notebook/specs/2026-04-28-log-system-design.md`
- 计划：`reference/self_notebook/plans/2026-04-28-log-system.md`
- loguru 官方文档：https://loguru.readthedocs.io/en/stable/api/logger.html
