---
code_file: src/narranexus/platform/services/service_audit.py
last_verified: 2026-08-26
stub: false
---

## 2026-08-26 — `_emit` / `event` 报告成败（仍然永不抛）

`_emit` 一直吞掉异常——这条不变，观察者不能打断被观察者，所有调用方都靠它。
但吞掉异常**顺带吞掉了结果**：调用方分不清「写进去了」和「DB 当时是挂的」。

对绝大多数调用方无所谓（心跳、生命周期事件写不进去就算了）。对**据「写成功」
缓存了一个决定**的调用方是致命的：[[step_3_agent_loop.py]] 的 DM 兜底审计要
「同一个对话每窗只记一行」，它按「`event()` 没抛异常」来 arm 冷却——于是 DB
抖一下，那个对话接下来整个窗口都不再尝试写审计行，哪怕 DB 两秒后就好了。而
这条审计存在的唯一理由就是「日志会轮转、DB 行不会」。

所以 `_emit` / `event` 返回 `bool`。`started` / `stopped` / `error` /
`heartbeat` 的调用方忽略返回值，签名不变。

**别把这理解成「可以抛了」**——返回 False 就是它报告失败的全部方式。

**这条链上每一环都吞自己的异常，所以「没抛」在每一环都恒真。** 第一版
`_emit` 只是 `await repo.record(...)` 然后 `return True`，而
[[service_audit_repository]] 的 `record()` **自己就 catch 了 insert 异常**
——于是「landed write」这个说法只覆盖了「拿不到 db handle」，插入失败照样报
成功，上面那个冷却还是会在 DB 故障时被 arm。现在 `record()` 也返回 `bool`，
`_emit` 直接把它透传出去。

判据：这个性质必须能在**真实仓储**上验证，不能只在假货上。链级覆盖在
`tests/services/test_service_audit_write_outcome.py`——它拿一个 insert 会抛
的 db handle 驱动真的 `ServiceAuditRepository`。

## 2026-08-21 — 公开 `event(event_type, detail)`

之前公开面只有 `started/stopped/error/heartbeat`,任意事件名要调私有 `_emit`。
[[_message_bus_mcp_tools.py]] 记 `inbox_write_failed` 需要一个生命周期之外的事件名,
故加公开 `event()` 一行委托 `_emit`,让调用方别再碰私有符号(私有符号可被合法重命名,
调用方会静默失去审计行)。与其余入口一样**永不抛**。

## 2026-08-17 — `_last_heartbeat_at` 初值改为 `-inf`

与 [[channel_trigger_base.py]] 同一个 bug 形状：门禁是
`time.monotonic() - _last_heartbeat_at < heartbeat_interval`，而 monotonic 在
Linux 上从开机计数，所以 `0.0` 读作"在开机那刻发过心跳"，宿主机比 interval
（默认 60s）年轻时第一拍被静默跳过。改成 `float("-inf")`，让"从没发过"成为真正
的哨兵。窗口只有 60 秒、且会自愈，所以影响比 channel trigger 那两处小得多，但
写法不一致本身就是下一个人照抄时的坑。

调用点 `module_poller` / `job_trigger` / `message_bus_trigger` 都不传 `force`，
所以它们的首拍确实走这道门。守卫见
`tests/channel/test_first_cycle_on_a_fresh_host.py`。

# service_audit.py — 长跑后台循环的 L2 可观测性助手（ServiceAuditor）

## 为什么存在

源自事故教训 #4/#5：EC2 上的 JobTrigger / ModulePoller 只有 L1（"进程还活着"）
可观测性，poll 协程一旦卡死，进程看着健康但其实没干活；而应用日志在
`docker restart` 时会丢。本助手让任何长跑循环一次接线，就在 DB 留下黑匣子轨迹
（started / stopped / heartbeat / error）。**心跳过期或缺失**这件事本身就暴露了
`ps` 抓不到的僵尸。

## 一个循环怎么用

init 时构造一个 `ServiceAuditor("<service>")`（很便宜——DB client 首次写入才惰性
获取）。`started(detail)` 一次；每个 poll cycle `heartbeat(detail)`；关闭时
`stopped()`；except 里 `error(str(e))`。

## 为什么 heartbeat 带计数器

`heartbeat()` 有节流（默认 60s），免得 5s 的循环刷爆 DB。detail 里带累计工作计数
（如 `enqueued_total`）——这就是区分"卡死"和"空闲"的关键：新行但计数冻结 = 在空转，
旧行 = 循环彻底停跳。

## 坑

- 所有写入都是 best-effort 且吞异常——**观察者绝不能拖垮被观察者**。丢一条审计行也
  好过卡住一个 poller。
- 持久化在 `repository/service_audit_repository`；表是 `schema_registry` 里的
  `service_audit`（auto-migrate 建）。本助手只是上面的节流 + 生命周期词汇层。
- 从 channel 专属的 `lark_trigger_audit` 泛化而来；新循环应复用本助手，别再为每个
  服务新建一张审计表。
