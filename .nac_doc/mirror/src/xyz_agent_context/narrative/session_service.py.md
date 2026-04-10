---
code_file: src/xyz_agent_context/narrative/session_service.py
last_verified: 2026-04-10
stub: false
---

# session_service.py — 用户 Session 持久化管理

## 为什么存在

连续性检测（`ContinuityDetector`）需要知道"上一次用户问了什么"、"当前绑定在哪条 Narrative"。这些信息如果只存在内存里，重启进程就会丢失，用户重新开口时系统认不出是同一个人的延续对话。`SessionService` 提供双层存储——内存 dict 做热缓存，JSON 文件做冷持久化——保证跨进程重启后 Session 仍然可恢复。

它是有意不写数据库的：Session 数据体量小、生命周期短（10分钟超时），用文件比用数据库表更轻量，也避免了对数据库连接池的依赖。

## 上下游关系

**被谁用**：`agent_runtime/_agent_runtime_steps/step_1_select_narrative.py` 调 `get_or_create_session()` 取当前 Session，传入 `NarrativeService.select()`；select 执行后调用 `session_service.save_session(session)` 将被修改过的 Session 持久化。`backend/routes/` 在前端请求时偶尔调用 `get_session_count()` 做监控。

**依赖谁**：只依赖 `narrative/models.py` 里的 `ConversationSession` 和 `config.py` 里的 `SESSION_TIMEOUT`，无外部 IO 依赖（除了文件系统）。使用 `asyncio.Lock` 保护内存字典，使用 `fcntl.flock` 保护文件写入。

## 设计决策

Session 文件路径格式是 `{agent_id}_{user_id}.json`，存在项目根目录的 `sessions/` 下。曾考虑用数据库表，但多 Agent 同时运行时每次请求都要查表会增加不必要的延迟，且 Session 超时后要清理，文件删除比 SQL DELETE 更直接。

`get_or_create_session()` 的超时判断是**每次请求时主动判断**，而不是后台定时清理。好处是无需独立清理线程；坏处是如果某个用户从不再发消息，其 Session 文件永远留在磁盘上——因此也提供了 `cleanup_expired_sessions()` 供外部定期调用。

文件锁用的是 `fcntl.flock`，是 Unix-only 的实现。在 Windows 或某些受限环境下会崩溃（这是 Linux/macOS 优先的项目，暂时接受这个约束）。

## Gotcha / 边界情况

`get_or_create_session()` 和 `get_session_by_agent_user()` 的语义不同：前者会做超时检测并可能创建新 Session；后者只读取，不会触发超时，也不会创建新 Session。别在应该用前者的地方用后者，否则用户在超时后发消息会得到旧 Session 而不是新 Session。

Session 里的 `current_narrative_id` 是由 `NarrativeService.select()` 在返回前直接写入 session 对象的（可变引用修改）。`save_session()` 必须在 `select()` **之后**调用，否则文件里存的还是旧 narrative_id。

`last_query_time` 在文件里序列化为 ISO 格式字符串，反序列化时会经过 `_ensure_timezone_aware()` 补上 UTC timezone。如果手动修改 Session 文件写入了不带时区的时间字符串，加载时会被当作 UTC 处理，不会报错但可能导致超时逻辑偏差。

## 新人易踩的坑

在多进程部署下（比如 Gunicorn 多 worker），每个进程各自维护独立的内存 dict，但共享同一套文件。文件锁能保证写入互斥，但同一个用户的 Session 可能被不同 worker 分别加载进各自内存——这意味着内存 cache 不跨进程共享，只有文件是真相来源。在这种场景下，每次请求都应走文件加载路径，可以把内存 cache 视为本次进程内的性能优化，不能视为权威。
