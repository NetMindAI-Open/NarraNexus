---
code_file: frontend/src/hooks/useRunObservation.ts
last_verified: 2026-08-30
stub: false
---

## 2026-08-30 — 档位接进来了，合并同时加了同档守卫

这条路（`run_recorder` → [[broadcaster]] →
[[wsManager]]`.translateReconnectFrame` → 本 hook）现在全程带档位，所以
**[[TeamMemberPanel]] 的 live 视图第一次能显示进度档**。

**接档位和加守卫必须同一次做完**，本次即如此：本 hook 的 `agent_thinking`
分支原来**无条件**并进上一个 thinking 块；档位一旦流进来，那个无条件合并会
立刻开始跨档合并、把两档文本标成一档——[[chatStore]] 那边专门堵过的同一个
失败。现在两边同一条守卫（`!!last.monologue === isMonologue`），
夹具在 `__tests__/useRunObservation.test.ts` 的
「narration tier on the observation path」。

## 2026-08-24 — startedAt 解析改走 [[../lib/backendTs]](#349 I1)

`run_reconnect.started_at` 原来裸 `Date.parse`——云端 MySQL 发的是
naive-UTC 串,会被按本地时区解;今天没有 UI 读 `startedAt`,但字段注释
承诺 "elapsed anchor",谁先渲染谁先偏 480 分钟(CST)。与 wsManager 的
同字段消费点共用一份解析规则,「两个面不能漂移」的 docstring 承诺重新
成立。测试补了 naive 形状用例(原有的 'Z' 用例钉的是后端不发的形状,
永远绿)。

## 2026-07-31 (三次) — 退避阶梯只认进展帧 + fatal 协议错停梯（review R2 #2）

原来任何帧（含 error）都把 attempts 归零，而服务端 Forbidden/NotFound/
DBError 都是「error 帧 + 立即 close」→ 1s 一次的重连风暴。现在：
error 帧不重置阶梯；三种协议终态错误置 `fatalRef`（同步旗标——
endedRef 经 effect 迟一拍，会输给紧随的 onclose）并合成
run_ended(failed) 让快照落终态。TeamMemberPanel 相应在
errorMessage+零事件时显示 detailLoadFailed 而不是永转的 startingUp。

## 2026-07-31 (二次) — run_reconnect = 回放重启（review Important #1）

观察端点每次 attach 都从 seq 0 全量回放，而重连梯子不换 effect key ——
reducer 若在旧快照上叠新回放，整条 trace 翻倍（tool_output 无条件
push 必重复）。修法取协议语义：`run_reconnect`（回放必然首帧）分支从
INITIAL 起算，「收到 run_reconnect = 一段完整回放开始」自带幂等，未来
任何新的重放入口免疫。测试锁定（reconnect replay does not stack）。

# useRunObservation.ts — 任意 run 的前端观察通道

## 为什么存在

run 可观察性是平台属性（后端见 run_recorder.py.md）：任何 run 的
live trace 都由同一个 WS 观察端点（`/ws/agent/run?run_id=X`，回放 +
直播/tail-follow）供给。这个 hook 是它的前端半身：给 run_id，返回一份
活的快照 `{status, endState, events, steps, startedAt, errorMessage,
opsCount}`，任何界面（今天 = team roster 的成员详情
[[TeamMemberPanel]]；明天 = dashboard 看 trigger run）都能用共享的
process 组件渲染它。**只读**：观察绝不启动/停止/引导 run（铁律 #14）。

## 设计决策

- **帧翻译复用 `translateReconnectFrame`**（wsManager 导出）——
  聊天重连和观察是同一个端点的两个消费者，一个翻译器保证两个面
  不漂移。
- reducer（`applyObservationFrame`，纯函数、导出可测）镜像
  chatStore.processMessage 的积木规则：thinking 连续段合并、pending
  tool 按 tool_call_id 原位替换、plan 全量快照 replace-on-write、
  流水线 phase 按 step id upsert 进 `steps`。但**观察者视角**：不收
  agent_response delta（被观察 run 的回复落在它自己的面上——房间
  transcript），不做 reply 抽取。
- 连接生命周期：enabled=false / runId 空 → 不开 socket（折叠的
  roster 行零成本）；非终态断线 → 1s→10s 封顶退避重连（run 比任何
  观察 socket 都长寿）；run_ended/complete → 停梯。环境无 WebSocket
  时静默降级（不崩 roster）。

## Gotcha

- `endedRef` 在 effect 里同步（render 期写 ref 是 react-hooks 新规则
  的 error）。
- 跨进程 run 的 thinking 以整段到达（tail-follow 是段粒度）——
  观察者比 run 主人晚一个段落看到 thinking，契约如此。

测试：hooks/__tests__/useRunObservation.test.ts（reducer 全分支）。

## 2026-08-12 — 熔断是终局，且不再无限重连

`agent_circuit_open` 此前落进普通 error 分支：写一条 errorMessage、**不置 ended**，
于是 `onclose` 一直退避重连——**对着一个定义上就在拒绝运行的 agent**。房间显示
「Couldn't load the process」，客户端在后面循环重试。

现在它是终局帧，并保留 `circuitReason`（`paused:auth` / `paused:quota` / `cooling`）。
**没有折叠成 `failed`**：三种原因分别要求换 key、充值、或仅仅等待，
「失败」等于告诉用户什么也别做。

**`isTerminalErrorFrame` 抽成导出的纯函数不是为了整洁。** 决定重试阶梯的是 `fatalRef`，
它由这个判定设置，而**任何 reducer 测试都碰不到它**——reducer 永远看不到 `onclose`。
这正是熔断能无限重连而全部测试保持绿色的原因，而「观察 socket 不无限重连」是 PRD 明写的验收标准。
抽出来之后，同一个变异立刻变红。

## 2026-08-18 — artifact_changed 分流 + 重连自愈拉取

onmessage 里 artifact_changed 不进 timeline reducer,分流给
artifactStore.applyEvent(repointed 另推诚实 toast:旧→新路径+hash 是否
验证);onopen(含重连)对当前 agent 触发一次全量拉——断线期间丢的事件因此
无关紧要(自愈地板)。
