---
code_file: frontend/src/lib/backendTs.ts
last_verified: 2026-08-24
stub: false
---

# backendTs.ts — 后端时间戳字符串的唯一解析规则

## 为什么存在

#349 I1:同一条 `run_reconnect` 帧的时间字段有三个消费点,各自裸
`Date.parse`,其中一处修了 naive-UTC、两处没修——「一个对一个错」的
漂移最难发现。规则收敛到一个函数:带显式偏移(Z / ±hh[:]mm)的串
原样信任(**绝不**盲目追加 'Z'——会把 `...+00:00` 变 Invalid Date);
无偏移的串按 UTC 解。

## 上下游关系

**被谁用**:[[../services/wsManager.ts]](`input_timestamp` +
`started_at`)、[[../hooks/useRunObservation.ts]](`started_at`)。
新的 run_reconnect / 观测帧时间戳消费者**必须**走这里,不许再裸
`Date.parse`。
**根因侧**:后端 `_format_dt`(backend/routes/websocket.py)已给
naive 值补 UTC,本解析器保留双形状容错——后端再回归一次也不至于
二次翻车。

## Gotcha

- SQLite 路径的串天生带 `+00:00`、MySQL 路径修复前是 naive——本地开发
  环境**永远复现不出** naive 分支,测试必须显式喂无后缀形状
  (环境轴;见 [[__tests__/backendTs.test.ts]])。
