---
code_file: frontend/src/components/chat/team/TeamMemberPanel.tsx
last_verified: 2026-08-31
stub: false
---

## 2026-08-31（二）— 已结束的观察不再谎称 streaming

`<TurnTimeline isStreaming />` 改成 `isStreaming={observation.status !== 'ended'}`，
和紧邻的 `LiveCursorRow` 用同一个判据（那一行本来就写着 `!== 'ended'`）。

不是回归——退役的 `ProcessEventRows` 一直是纯文本。但它和本次「直播 == 落定
== 回放同一形态」的主张对不上：**已结束的成员 turn 会走 `whitespace-pre-wrap`
而不是 `<Markdown>`**，而主聊落定的同一批事件是渲染 markdown 的。同一份事件
在两个面上长得不一样，正是这一版要消灭的东西。

## 2026-08-31 — 团队卡**保留**边框，和单聊分道

单聊的过程框今天拆了（见 [[../ChatPanel]]），本组件的卡片 chrome **不动**。
两者不是同一个面。这里是**成员名册里的一行展开**，同屏可能有好几个成员，
框正是把成员彼此分开的东西；单聊只有一个 agent、一列阅读动线，那里的第二个
框只会和 turn 抢注意力。文件头已写明这条取舍，免得下次有人来「统一」。

相位白名单与 `phaseSettled` 的共享规则不变，文档里的对照方改名为
[[../process/RunPhases]]。

## 2026-08-30（二）— 观察视图换成同一个渲染器

live 分支从 `ProcessEventRows` 换成 `<TurnTimeline events={processEvents}
isStreaming />`，与主聊天的直播态、落定态同一份实现。settled 分支本来就走
TurnTimeline，所以本面板内部的两种形态也一并消失。

`useNarrationTier` 不再在本文件解析——色调由 TurnTimeline 自己决定，面板
不必知道。

## 2026-08-30 — 独白提级偏好与 ProcessPanel 同一份写法（且已真的生效）

读 [[uiStore]] 的 `interimNarration`（经 [[useNarrationTier]]）传给
`ProcessEventRows`，与 `ProcessPanel` 逐字相同（铁律 #8：同一功能件两侧同
规则）。

喂本面板的是 [[useRunObservation]]，那条回放路**本次也补上了档位**，所以这里
不是预铺——团队房里看成员过程，叙述就是进度档。

## 2026-08-26 — 阶段行同步改白名单（跟随 ProcessPanel）

`phases` 过滤从 `!startsWith('3.4')` 换成 `PHASE_STEP_IDS.has(step)`（见
[[processShared]]，从 `PHASE_LABEL_KEYS` 派生），与单聊 `ProcessPanel`（今 [[../process/RunPhases]]）
同一套白名单 —— 挡掉 `3.5/4/5` 等未映射步骤泄漏英文 title。落定判据也收敛
到共享 `phaseSettled`，**传未过滤的 `observation.steps`**（此前这里读的是
已过滤的 `phases`，白名单后就看不到 >3.4 的落定信号了）。改一处两边跟
（铁律 #8）。

## 2026-08-07 — chrome 头条上的停止按钮 + 三态

running 且有 `event_id` 的成员,头条右侧出现停止按钮。挂在这里是因为这张
卡片已经持有停止所需的两件东西(#219 铺的):`event_id`(就是 run_id)和
live 状态。

**三态 = running →(点击)→ stopping →(观察流给终态)→ stopped**。
`stopping` 只覆盖"请求已记录"到"run 真的停了"之间的空档 —— 那 8 分钟黑箱
要的就是这个即时答复,所以按钮**不等** `api.cancelRun` 的 resolve 才变样,
点下去立刻转 stopping。

`stopping` 有**三条出口**,少一条就会挂住:

- **观察流给终态** —— 正常路径。
- **请求失败**(403 / run 已消失 / 断网)→ 落回可点状态并显示 `stopFailed`,
  而不是永远转着 spinner —— 那等于用新的黑箱换旧的黑箱。
- **`already_settled`** —— run 在渲染与点击之间自己跑完了。此时服务端**没有
  盖旗标**,也就不会有任何终态帧到来;不清掉 `stopping` 就会永远等一个已经
  发生过的事件。语义见 [[api]](「前端据此知道『没什么可停的』,而不是当成
  失败」)。

其余:

- **停止请求按 run 作用域**:state 里存 `{runId, failed}`,`runId` 与当前
  `activity.event_id` 不符就当没有。否则同一成员开下一轮时,按钮会因为上
  一轮的 pending 停止而灰着,而没人要求停这一轮。
- **cancelled 必须与 completed 可区分**:`observation.endState ===
  'cancelled'` 时显示 `stopped`。把用户掐掉的任务显示成"已完成",等于告诉
  他那 25 分钟还是跑完了。
- **`useRunObservation` 保持只读**:取消走独立的 REST 调用,观察通道只负责
  看到结果。它的 mirror 明写"观察绝不启动/停止/引导 run"(铁律 #14),
  从这里发起取消会破坏那个契约。

前端不做 owner 判断 —— 服务端 403 是唯一边界(见
[[runs]]),按钮可见性只是提示。

新 i18n:`chat.team.roster.stop|stopping|stopped|stopFailed|stopHint`,
10 份 locale 齐。测试:`__tests__/TeamMemberPanel.stop.test.tsx`。

## 2026-07-31 (二次) — 观察通道 fatal 错误的诚实展示

live 成员 + observation.errorMessage + 零事件 → 显示 detailLoadFailed，
不再挂着永远兑现不了的 "Starting up…"（配合 hook 的 fatal 停梯）。

# TeamMemberPanel.tsx — 成员详情 = 迷你 ProcessPanel

## 为什么存在

2026-07-31 Owner 反馈：roster 展开后的运行视图（灰点素行的
PhaseTimeline）和单聊 ProcessPanel 是两个世界，「预期是 chat 那个
ProcessPanel 差不多精致的」。本组件把成员详情做成同一张终端卡：
chrome 头条（LiveDot 呼吸灯 + `name · process` + ops/elapsed）、
`✓`/spinner 阶段行、`$` 工具行、`❯▌` 活光标、follow-scroll、plan
页脚 —— 全部复用 processShared 的共享件，与单聊同一套字形语言。

## 数据源（两层，且不是装饰性区分）

- **running/stalled** → [[useRunObservation]](activity.event_id)：
  真·实时 thinking/tool 流（bus 活动行运行中就带 event_id，#199 系列
  铺的钥匙）。观察 socket 连上前（~0-1s）显示诚实的 "Starting up…"
  兜底，不假装知道更多。
- **idle** → useTurnDetail 的持久化 event_log + TurnTimeline 渲染器
  （2026-07-31 已拍板的 reasoning & tools 视图），活在 430px 宽里。
  上一 turn 的结果绝不显示在当前 turn 下（settled key 校验，沿袭
  v1 语义）。

## Gotcha

- `open=false` 时返回 null 且 observation `enabled=false` —— 折叠行
  零 socket。
- stalled 成员观察照常工作（tail-follow 一直跟到心跳死），卡片底部
  额外亮 amber 的 silentFor 行。
- elapsed 用父级共享 `now` 时钟 + poll 的 started_at —— 屏上所有
  时长同一口径（v1 决策沿袭）。

## 2026-08-12 — 暂停的 agent 说自己暂停了

此前熔断和真正的读取失败**共用同一句文案**（`detailLoadFailed`），两者无法区分，
而其中一句是假的。现在熔断有自己的文案，并复用 App.tsx 已有的
`narranexus:agent-circuit-open` 横幅（带一键 Resume）。

**为什么宣告在这里而不在 hook 里**：hook 只有 `runId`，拿不到 agentId；而且在 data hook 里
发全局事件是把副作用放在没人预期的位置。这个组件正好既知道是哪个 agent、又是显示误导文案的地方。

**复用既有事件而不另造横幅**：熔断原因的词表只该有一处。这个仓库刚为「一条规则两份实现」
付过学费（调色板色序已漂移）。

> 中间写过一个 `announced` ref 去防重复宣告，**变异验证反复活着——最后发现是那段代码本身多余**：
> `useEffect` 的依赖数组已经提供了「每个 (reason, agent) 一次」，`now` 变化根本不重跑 effect。
> 那个 ref 唯一的真实作用是压掉「熔断解除后又打开」时本该发生的再次宣告。**删掉，而不是为它补断言。**
