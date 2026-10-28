---
code_file: tests/backend/test_manyfold_im_ingress.py
stub: false
last_verified: 2026-09-23
---

The route fixture initializes `RunTasks` as production lifespan does, so detached
run and managed-audit task creation exercise the same shutdown ownership contract.

## 2026-09-11（PR #394 review 第六轮 N-2）— 静默摄取回执说真话

`test_base_managed_silent_ingest_drives_native_batch` 增加负例：静默批返回没跑成的原因时，
`managed_silent_ingest` 的回执是 `(silent group message not ingested - <reason>)`，不含 "ingested to memory"。
第六轮 M-3 起 reason 是稳定短码（用例用 `runtime_error`），不再带异常类名 / error_type。

## 2026-08-28（接线）— 托管面的熔断与铁律 #16 门槛

托管模式**绕开整条原生接收路径**——没有 `_subscribe_loop`、没有 dedup store、
没有 worker 队列、没有 `_process_message`——所以基类在 `start()` 里构造的 guard
在这里永远不会生效。没有自己的调用点的话，Manyfold 这个面就是唯一没有防护的
入口。

新增两条钉铁律 #16：**每条被丢的消息留一行**（不是每次跳闸一行——那答不出
「安静了多久、吞了多少」），且行里带得出「为什么」和「还要多久」。

写第二条时抓到一处真缺口：丢弃行的 `cooldown_seconds` 恒为 0——那个字段是
跳闸时赋的长度，丢弃时没人填。补了 `cooldown_remaining_seconds`，口径在所有
行上一致：跳闸行是全长、丢弃行是剩余、其余是 0（因为确实没在隔离）。

原生面的同一道门槛在 [[test_ingress_breaker_audit_trail.py]]——两个面走**不同
的审计调用点**，把原生那侧改成不写，这里的用例照样全绿。

## 2026-08-28（接线 review）— 托管面的清扫与 peer 标记

**托管行不能依赖同名原生 trigger 存在**：基类清扫按自己的 `channel_name` 作用
域，纯托管部署里那些行永远不会被扫到。断言清扫被调用、带对 channel、且按天
节流（三个回合只扫一次——每条消息扫一次等于把全表读放进热路径）。

**闸门要收到 `is_agent_peer`**：它判 agent 对端用的是**另一对**阈值，不是把
人类那对调低。所以这条用例的 fake 必须两对都覆写——只覆写人类那对（共享的
`_GuardedTrigger` 就是这样）会让 agent 对端按默认的 20 判定，风暴**根本不跳
闸**，而我第一版正是这么写的，失败信息还是「storm never tripped」这种看起来
像被测代码坏了的样子。

## 2026-08-28（接线 review 二轮）— 保留期天数与 guard 开关

**断言必须让基类值和覆写值不同**。原来的 fake 不覆写
`INGRESS_BREAKER_RETENTION_DAYS`，那么「断言等于 trigger 的值」在**两种实现下
都为真**——正是同一轮里 agent 阈值那条测试刚踩过的形状。现在 fake 覆写成 7 天
再断言等于 7，写死基类值的变异当场红。

**patch 目标随 import 位置**。生产代码把 `ChannelIngressBreakerRepository` 提到
模块级之后（与同文件的 `ChannelTriggerAuditRepository` 一致），patch 也要打在
模块上。打错的话什么都没 patch 到，真 repository 被调用、`calls` 为空，测试红在
「managed rows are never swept」上——读起来像被测代码坏了。

新增：熔断器关掉的渠道**不该被扫**。它写不出行，那次查询必然为空，而它发生在
用户消息的路径上。
# test_manyfold_im_ingress.py — 托管 IM 入站的映射与 route 接线

覆盖 `build_inbound_run_context` 的 provider 映射（channel_provider /
channel_context → WorkingSource + ChannelTag + extra_data）、
`ManagedChannelIngress` 的业务 hook 路由，以及 `/v1/chat/completions` 把映射
结果交给 `BackgroundRun.drive` 的那一段接线。

## 2026-08-26 — agent-sender 标记必须活过 route

新增两条**真实端点**测试（ASGITransport + `compat_app` 夹具）。

它们存在的理由是别处测不到：`run_input` 在 ingress hooks **之前**就渲染完
了，所以只有 route 能在 hooks 之后把它重建。而其余关于
`retag_managed_input` 的断言都是**直接调那个函数**——把
`openai_compat.py` 里那行调用删掉，那些断言照样全绿，标记却从模型读到的
字符串里消失了。

断言落在 `drive` 的 `input_content` 上，也就是真正送进 agent 的那个串。
变异验证：删掉 route 那行 → 立刻红。

替掉 NarraMessenger 的 `managed_before_run`（fail-closed 授权门）是必要的：
授权是另一个关注点，放它跑会 deny 掉这一轮，然后还要一个 db 去写 deny 审计。
**替掉不等于手抄**——盖章与重渲都走真实代码。

只覆盖 narramessenger：它是全仓唯一答得出 `is_agent_peer` 的渠道；其余托管
渠道恒 False、重渲前后字节等价，由 `test_agent_peer_signal.py` 那条
「人类托管 turn 字节不变」钉住。
