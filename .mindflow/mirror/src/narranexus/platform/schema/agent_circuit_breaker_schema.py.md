---
code_file: src/narranexus/platform/schema/agent_circuit_breaker_schema.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（PR #394 review 第四轮 I-1）— `probe_claimed_at` 换成 `probe_run_id`

`AgentCircuitBreaker.probe_run_id: Optional[str]`：认领者自己的 run（`events.event_id`），
由认领的 turn 在 run 行建好后绑定；无认领或尚未建行时为 NULL。崩溃窗口兜底只问「这一行还活着
吗」，不再问「有没有认领之后才开始的 run」。`probe_claimed_at` 字段删除（本 PR 引入、未发布）。

# agent_circuit_breaker_schema.py — 实时层 Agent 熔断器数据模型

## 为什么存在

实时对话层（BackgroundRun + 各触发入口）缺少熔断：一个 key 死了/余额耗尽的 Agent 会被
无限重触发、白烧轮询资源（Job 调度层早有熔断，实时层没有）。这个模型是熔断状态的
Pydantic 定义，落在**独立表** `instance_agent_circuit_breaker`（键 agent_id），不往
`agents` 表加列（范围决策 #1）。

## 关键设计：按成因分而治之

`ErrorCategory`（auth/quota/transient/business）驱动升级策略：
- **auth/quota**（不会自愈，要人改 key/余额）→ 连续 3 次同类失败 PAUSE + 告警 owner。
- **transient/business**（会自愈，或用户自选的 flaky 模型）→ **永不硬暂停**（铁律 #15
  禁止平台放弃用户选的模型），退避封顶 1h 永远重试。

因此 `PausedReason` 只有 `auth`/`quota` 两个值——没有 `repeated_failure`（transient 从不
硬暂停）。`QUOTA` 也是未来"Executor 批量余额不足"的接入点。

`ErrorCategory` 四类**都在用**：TRANSIENT 是**正面识别**的 provider 侧瞬时错（通知 owner），
`BUSINESS` 是真正的残余桶（我们的 bug / 永久客户端错 / 认不出的），持续失败时**只报平台方、
不发 owner**——把"我们的 bug"和"用户 provider 侧的问题"分开，避免拿自己的缺陷去骚扰用户。

## 2026-09-10（PR #394 review I1）— 新增 `probe_claimed_at`；PROBING 只由持 token 者结算

`probe_claimed_at: Optional[datetime]`：认领时刻，与 `probe_token` 同写同清。token 本身由
赢得认领的 turn 在进程内携带并用于结算；这一列只服务崩溃窗口（认领者死了、token 随之消失）
的兜底——只把「认领之后才开始」的存活 run 当作可能的认领者。`CbStatus.PROBING` 的注释改为
「只有持 `probe_token` 的 turn 能结算」。

## 2026-09-10 — 状态机加 PROBING（半开）与 `probe_token`

`CbStatus` 四态：ACTIVE → COOLING（退避）→ PAUSED（auth/quota 连续 3 次）→ **PROBING**
（半开：PAUSED 的延迟到期后恰好一个 turn 被放行去探测）→ 成功回 ACTIVE / 失败回 PAUSED
（延迟翻倍）。PROBING 只可能源自 auth/quota 的 PAUSE，所以 `PausedReason` 仍只有
`auth`/`quota` 两个值，这段不变。

`cooldown_until` 一列三义：COOLING 的退避到期、PAUSED 的半开延迟到期、PROBING 的探测
grant 到期——服务层按 `cb_status` 解释它，别再往这列上叠第四种含义。新增 `probe_token`
（nullable）专门做认领的 compare-and-swap 键：每次成功认领写新随机值，写回其他任何状态时
置 NULL；它不表达业务状态，只保证"认领前后值不同"。

## Gotcha

- `model_config = {"use_enum_values": True}`：从 DB 行构造出来后，`cb_status` /
  `failure_category` / `paused_reason` 是**字符串值**（不是枚举实例），比较时用
  `x == ErrorCategory.AUTH.value`。
- `failure_category` 记录当前失败连击所属的类别；类别一变就重置连击计数，保证"连续 3 次"
  是**同类连续**，不被无关抖动稀释。
