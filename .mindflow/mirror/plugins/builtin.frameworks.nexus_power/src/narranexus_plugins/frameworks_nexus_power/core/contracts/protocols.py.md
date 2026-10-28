---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/contracts/protocols.py
last_verified: 2026-09-03
stub: false
---

## 2026-09-03（批 2a.5）— `ToolExecutor.model_tools()`

协议新增方法（visible 的子集）；测试里的假执行器都实现为 `return self.visible_tools()`。

## 2026-07-31 — ToolChannel.list_tools 的顺序契约写进协议

dispatcher 不再排序(C2 落地)后,「确定性、append-only 顺序」成为**通道的
义务**——新通道若注册序不确定或重排,静默打穿缓存前缀(只表现为命中率掉)。
契约写进 list_tools docstring;真实通道各有顺序断言(BuiltinToolset 构造
确定性、McpToolChannel 批次追加,tests/nexus_power/test_tooling.py)。

# contracts/protocols — 13 个组件 Protocol

「接口一次到位、实现分期长大」的载体;loop 只 import 本文件与 events。判据:加第二个实现时 diff 不碰既有类。LedgerView 只读协议实现读写分离(策略拿不到写账本的刀);CancellationSignal 结构化重声明平台 CancellationView,保住 L0 零平台依赖。

## 2026-08-23(补)— SteeringInlet.take_consumed

`SteeringInlet` 加可选 `take_consumed() -> list[str]`(默认 `[]`):返回自上次调用以来 drain 掉的 steer_inbox 行 id 并清空,
让 loop 能发 `TYPE_STEER_CONSUMED` 报消费、驱动 producer 只对真被读到的行推游标。`QueueSteeringInlet` 实现(累积
被剥的 `_steer_id`),`NullSteeringInlet` 恒空。见消费契约([[message_bus_trigger.py]] 补5)。

## 2026-08-24(补)— take_consumed 无默认体

`SteeringInlet.take_consumed` 方法体改 `...`(不留 `return []`):Protocol 默认体对结构化实现者不生效,而 loop 无条件
调它,所以每个 inlet 必须自定义(下一个——云端 executor 的 inlet——也是)。让「必须实现」在类型层直说。

## 2026-08-24(补)— SteeringInlet.wait_for_input 阻塞孪生 + 新增 WaitRequest 协议

* **`SteeringInlet.wait_for_input(timeout, cancel: CancellationSignal | None = None) -> list[ProviderMessage]`**:`drain` 的阻塞孪生。BLOCK 至多 `timeout` 秒等下一批消息、融合返回;超时/取消返 `[]`。loop 只在 agent 调 `wait_for_input` 工具那步调它,普通边界不调。`cancel` 鸭子类型在 `requested()`,必须在等待中及时打断(不只在 deadline)。无源 inlet 立即返 `[]`。(M2:注解去掉多余引号,`from __future__` 已惰性化,与实现拼法统一。)
* **新 `WaitRequest` 协议**:loop 的 wait 座位——per-turn holder,`wait_for_input` 工具写、loop 在 WAIT 边界 read-and-clear。`pending: Optional[float]` 是**已 clamp** 的秒数或 None。**订正(I1)**:类型化只让 `LoopAssembly.wait` **构造点**被检查;loop 经 `Any` assembly 读 `a.wait.pending`,静态查不到,故 clamp 靠**运行期**兜底。**协议现含 `request(raw) -> float`(I2)**:这是唯一 clamp 门(界 [MIN,MAX]、缺省/垃圾/NaN→default、存 `pending` 并返回)。把它写进协议(非只 `WaitState` 的约定),第二个 producer(executor /steer 的 holder)必须走同门 clamp,而不是裸写 `pending`——裸写绕过边界=bug(测试替身也一样)。具体实现见 [[wait_channel.py]] `WaitState.request`。
