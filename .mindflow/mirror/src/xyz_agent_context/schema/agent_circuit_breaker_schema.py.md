---
code_file: src/xyz_agent_context/schema/agent_circuit_breaker_schema.py
last_verified: 2026-07-13
stub: false
---
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

## Gotcha

- `model_config = {"use_enum_values": True}`：从 DB 行构造出来后，`cb_status` /
  `failure_category` / `paused_reason` 是**字符串值**（不是枚举实例），比较时用
  `x == ErrorCategory.AUTH.value`。
- `failure_category` 记录当前失败连击所属的类别；类别一变就重置连击计数，保证"连续 3 次"
  是**同类连续**，不被无关抖动稀释。
