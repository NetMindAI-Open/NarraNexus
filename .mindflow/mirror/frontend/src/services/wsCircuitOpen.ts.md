---
code_file: frontend/src/services/wsCircuitOpen.ts
last_verified: 2026-07-13
stub: false
---
# wsCircuitOpen.ts — 检测 WS "熔断器打开" 帧

## 为什么存在

后端 fresh-run 闸门在 Agent 被熔断（paused/cooling）时发一帧
`{type:'error', error_type:'agent_circuit_open', cb_reason:'paused:auth'|'paused:quota'|'cooling'}`
并关闭 socket。若无处理，用户只看到一个红气泡。这个 helper 让 wsManager 识别该帧并派发
app 级事件，App.tsx 弹出带"Resume"按钮的横幅——与 auth-expired 路径（wsAuthError.ts）对称。

## 上下游关系

`isCircuitOpenMessage` / `circuitOpenReason` 被 `wsManager.run()` 的 onmessage 调用；
`dispatchAgentCircuitOpen` 派发 `narranexus:agent-circuit-open`（detail: {agentId, reason}），
App.tsx 监听后渲染横幅，其 Resume 按钮调 `api.resetAgentCircuitBreaker`。

## 设计决策

从 wsManager 抽出以便单测（无需真 WebSocket）。只在 fresh-run 路径出现（reconnect 针对
已存在的 run，不过 fresh-run 闸门），所以只 wsManager.run() 接线。
