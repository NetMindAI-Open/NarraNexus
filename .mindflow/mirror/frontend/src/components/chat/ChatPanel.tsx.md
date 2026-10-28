---
code_file: frontend/src/components/chat/ChatPanel.tsx
last_verified: 2026-09-11
stub: false
---

## 2026-09-11 — the header's Model & framework panel is back (OWNER-REQUIRED)

Reverses the 2026-08-27 (2) removal below: `agentCfgOpen` / `modelReloadKey`
state and the `<AgentLlmConfigPanel>` at the bottom of the panel are restored,
opened by [[ChatHeader]]'s `onOpenAgentConfig`. The handler is passed only when
the viewer owns the agent (`isOwner` = `currentAgent.created_by === userId`, the
profile page's gate), and the panel is mounted only then too. On save it bumps
`modelReloadKey` → `ComposerModelBadge reloadKey` (the chip re-reads a changed
model) and calls `refreshAgents()` so the agent list's model/framework
projection (profile page, dashboard) is fresh. Owner-required entry — keep it
even though the profile page and the sidebar ⋯ menu open the same panel.
Pinned by `__tests__/chatPanelAgentConfigEntry.test.tsx`.

## 2026-09-08 — 问候本地化抽到 `lib/bootstrapGreeting.ts`

覆盖带名字的问候；去重逻辑在 `buildTimeline`。

## 2026-09-04 — `handleSubmit` 一次只跑一个

`submittingRef` 门：`await encodeOutgoing`（读 awareness）在 composer 清空与 `startStreaming`
之前，`isLoading` 那时还是 false，第二个 Enter 会把整条路径再跑一遍、同一句话发两遍。这条在
dev 上因 `getAwareness` 已存在，不是 studio 引入，但同一处修最省。测试
`chatPanelSubmitGuard.test.tsx`。早条里 `builderPrompt.ts` 的 wikilink 降为反引号（该文件
同 PR 内创建又删除，仓库里从未存在）。

## 2026-09-03 (评审修订) — 落定沿按 agent 记 + 已应用消息去重

`wasStreamingRef` 原是跨 agent 共用的一个布尔，而 `isStreaming` / `messages` 是从
`activeAgentId` 派生的扁平字段。可复现丢数据路径：B 已落定并应用 → 用户在面板里
手改 name / awareness 落库 → 切到 A 发消息（A streaming）→ **A 还在跑时**切回 B：
扁平 `isStreaming` true → false，被判成「B 落定」，`applyFromReply` 重跑 B 的旧草稿，
两个 PUT 把用户手改**全部覆盖回旧值**，无备份无提示。多 agent 后台并跑是核心场景，
这直接否证了整个协议的立论「面板手改是权威」。

现在两道闸：`wasStreamingByAgentRef` 按 agent 记沿；`appliedReplyIdsRef` 以
`agentId:message.id` 记「已应用」，命中即 return。去重键不能用内容 —— 模型被允许
在后一轮重述同一份草稿。测试 `chatPanelStudioSettle.test.tsx` 复现上述四步。
这条修完 [[../../lib/builderProtocol.ts]] 的空串保护**仍然必需**：本条只防重放，
不防新草稿本身是空的。

## 2026-09-03 (修订 09-03 早条) — studio 改为每轮包裹 + 落定即应用

早先那条写的是「包裹**第一条**消息」，随结构化面板一起作废。现在：

- 提交路径 `outgoing = await encodeOutgoing(content)` —— studio 打开期间**每轮**
  都带 Builder 指令 + 当前配置。位置仍在 steer 分支之后（mid-run 追问不是一个
  回合）。返回原文即降级，studio 出问题不能吞掉用户的消息。
- 新增一个 effect 监听**流式落定沿**（true → false），把最后一条 assistant
  消息交给 `applyFromReply`。刻意不监听消息列表：草稿块只有回合结束才完整，
  流式中途应用会把半序列化的 JSON 写进 agent 的指令。

两个调用都来自 [[useStudioTurn.ts]]，本文件不含 studio 逻辑。

## 2026-09-03 — 创建工作室 v0：首条消息包裹 Builder 指令

提交路径新增 `outgoing`：agent 被 [[builderSession.ts]] 标记过时，用
`builderPrompt.ts`（同 PR 内创建又删除，仓库里从未存在）的 `buildBuilderFirstMessage` 包裹用户那句话，
`addUserMessage` 与 `run` 都发包裹后的内容。

**位置刻意放在 steer 分支之后** —— 一次 mid-run 追问绝不能烧掉这个标记。
标记是 consume-once 的，所以后续回合发的是纯文本。

指令为什么不能在进入聊天页时就发：它的作用是框住用户**自己**那句需求，而那
句话在用户敲之前并不存在。渲染侧由 [[MessageBubble.tsx]] 剥掉。
## 2026-08-31（四）— 「正在处理…」删除

直播块尾部那个 `Loader2 + chat.execution.acting` 的行内指示器删掉，i18n key
（en / zh 两处，其余语言从未有过）一并删除（铁律 #2）。

它原来的职责是「事件之间的空档里让页面别静默」。现在 [[process/RunPhases]]
在同一列顶部已经有 `» 运行 Agent` 的 spinner **加逐秒计时** —— 后者回答
「卡住还是在忙」比一句「正在处理…」更硬（计时器在动就是活的）。两个活体
指示器同屏是重复，而且它们在**同一列的两端**，读起来像两件事。

`Loader2` 的 import 保留：本文件另有一处在用（附件上传态）。

## 2026-08-31（三）— 相位来得太晚 + 直播轮次画了两次

Owner 报了两条，一条是我上一版留的门，一条是被我放大的旧账。

### 相位要等 agent loop 才出现

直播块的门是 `isStreaming && currentEvents.length > 0`，而 `currentEvents`
**要等 agent loop 产出才有第一行**。相位数据（`currentSteps`）在 step 0 就到齐，
却被这道门挡在外面——于是「初始化 / 选叙事 / 加载模块」全看不见，一直到
「运行 Agent」才突然全冒出来。[[process/RunPhases]] 存在的意义恰恰是填这段空白，
被门挡掉等于白写。门去掉：`isStreaming` 就渲染，空态由 RunPhases 自己说
「Starting up…」。

### 同一个回复画了两次

后端在 reply 工具执行时就**落库**，12 秒一次的 history poll 中途把它捞回来，
而直播块正用 `currentEvents` 渲染同一句话 —— 相位行上方和下方各一份。
刷新之后直播块没了，所以「刷新一次就好了」。

`buildUnifiedTimeline` 的 dedup **抓不到这种**：它调和的是 history ↔ **session
messages**，而在飞的这一轮还没有 session message（那是 `stopStreaming` 才写的）。
所以过滤放在 `visibleTimeline`：`isStreaming && eventId === currentRunId` 的
assistant 行不画。

**三个限定条件每一个都承重**：

- `isStreaming` + `currentRunId`：回合一落定这行就恢复成普通历史（铁律 #16
  —— 不是藏起来，只是不画两遍）。
- **`role === 'assistant'`**：自审时抓出来的。后端 `chat_history.py` 用**同一个
  循环**给两个 role 建行，**用户那一行也带着同一个 `event_id`**；而 history 行在
  dedup 里是赢家（session 副本被丢）。不限定 role 的话，**用户刚发的消息会在
  agent 干活期间从屏幕上消失**。测试钉住了这条。

## 2026-08-31 — 过程框拆掉：相位进文稿，plan 变贴底细条

Owner 验收文档流时问「为什么还留着一个 agent 过程的框」。`ProcessPanel` 那个
`rounded-lg + border + nm-paper + shadow` 的终端盒子确实是上一版的残留——
turn 已经无框了，它还坐在输入框上方，同一屏两种语域。

拆法是**去框不丢信息**（铁律 #16）：

- 相位 / ops / 计时 → [[process/RunPhases]]，渲染在直播块开头，`SegmentedReply`
  之上，和叙述同一列。
- plan → [[process/PlanStrip]]，仍钉在 composer 上方（它必须不滚走），但只剩
  一条 `border-t` 细线，不再是盒子。
- `ProcessPanel.tsx` 及其测试、mirror md **整体删除**（铁律 #2）。

## 2026-08-30（二）— 直播轮次即文档，turn 靠节奏分隔

两处改动：

- **直播块不再是"银色气泡里只放回复"**。此前直播只渲染 reply（过程在
  `ProcessPanel`），且套一个 silicon 气泡 + 头像。现在直接渲染
  `<SegmentedReply segments={segmentTurn(currentEvents)} showProcess isStreaming />`
  ——**落定时形状一个像素都不变**，因为它已经是最终形态。原来那个
  "有 reply 才渲染"的门也去掉了：叙述先于工具上屏正是要看的节奏。
- **turn 节奏**：用户消息的外层加 `mt-6`。没有气泡之后，分隔靠间距和
  用户气泡这个锚点。**刻意没用分隔线**——一轮一条横线在长对话里堆成流水账
  （取舍写进 `design_system.md` §2.6）。

`RingAvatar` 随 agent 侧头像一起从本文件退出。

## 2026-08-27 (3) — Activity Log 是只读页:整块 composer footer 按 tab 摘掉

**症状**:点进 Activity Log,下方还挂着 chat 输入框 —— 它是一个**独立的功能
页**(看 agent 自己发起的 run:job / channel / 队友),没有「回信」这条通道,
输入框在那儿就是个假承诺。

**改法**:`{!isActivityTab && ( … )}` 包住 `border-t` 那整块 footer,而**不是**
只藏 textarea。跟着一起消失的都是「对话专属」的东西,少摘一样就是不一致:

- `<Composer>` + 发送/Stop 键(steerable 时那颗 steer 键)
- attach(+)/`<AudioRecorder>`/隐私提示/`ComposerFastToggle`/`ComposerModelBadge`
- `transcriptionNotice`、pendingAttachments 预览行
- **`<ProcessPanel>`**(原本挂在 composer 上方,**没**带 tab 维度)—— 它描述的
  是 owner↔agent 这一轮的流程;而直播回复块本来就已经 `chatTab ===
  'conversation'` 门控过了,两者现在口径一致。

**顺带修的真 bug(铁律 #8 扫边)**:`handleDragOver` / `handleDrop` 挂在 **Card
根**(用户会往整个面板拖文件),footer 藏了它们照样收 —— 会在 Activity Log 上
静默上传并塞进 `pendingAttachments`,而**唯一能显示 chip 的预览行已经不渲染
了**:文件进了后端、界面一点痕迹没有。两个 handler 都加 `|| isActivityTab`
提前 return(`handleDragOver` 要在 `preventDefault` **之前** bail,否则浏览器
以为这里能放)。`handlePaste` 只挂在 textarea 上,textarea 不存在即失效,不用改。

**`isActivityTab` 的声明位置上移**到 `chatTab` state 旁边(不再和下面的零态
块放一起)—— 附件入口 handler(~L755)比零态块(~L910)早,拿不到原来的定义。

**草稿不会丢**:`<Composer>` 卸载时把文本 flush 进 per-agent 草稿 store,重挂
时恢复(见 [[Composer.tsx]]),所以「打了一半 → 切去 Activity Log → 切回来」
文本还在。

`bootstrapGreetingPending` 仍然**刻意不带 tab 维度**(见下面同日条):虽然现在
已经不可能从 Activity Log 发出第一条消息,但那是**渲染**条件收窄的结果,写入
条件不该跟着 UI 门控走。

测试:`chatPanelActivityTabComposer.test.tsx` —— 对话 tab 有 textbox/发送键/
attach 键、切 Activity Log 三者全无、再切回来又有;拖文件在对话 tab 会调
`uploadAttachment`(基线,证明 drop 真的到了 handler),在 Activity Log 上不调。

## 2026-08-27 (2) — 头部瘦身的连带清理:sessionLabel / AgentLlmConfigPanel

[[ChatHeader]] 的身份块改成 Profile 入口后,本文件三处状态成了死代码,
一并删掉(留着就是下一个人照抄的样板):

- `sessionLabel` 的 `useMemo`(以及 `formatChatTimestamp` import)——
  头部不再画那条 mono 侧标。
- `agentCfgOpen` / `modelReloadKey` 两个 state 与页面底部挂的
  `<AgentLlmConfigPanel>`——「Model & framework」面板现在**只从
  [[../../pages/AgentProfilePage]] 的 Settings tab 打开**,本页不再是
  它的宿主。
- `<ComposerModelBadge reloadKey>` 随之退化为 `<ComposerModelBadge>`:
  没有宿主面板在旁边保存,就没有需要 bump 的重读信号
  (见 [[ComposerModelBadge]] 同日条)。

composer 上的 model chip 仍是快速切模型的入口,不受影响。

## 2026-08-27 — 零态按 tab 分家（修「Activity Log 空时点了没反应」）

**症状**:agent 的 activity 流为空时,点 Activity Log 那颗按钮像是死键 ——
画面和「对话」tab 一模一样,用户到不了那个(本来就该空的)Activity Log。

**根因**:零态判定用的是 `historyLoaded && historyMessages.length === 0 &&
messages.length === 0`,**不带 tab 维度**,而它挂的两个界面
([[OnboardingJourney]] 的「<Agent> is ready」卡、bootstrap 问候气泡)都是
**对话专属**的。于是:

* 新 agent(两条流都空)→ 切到 Activity Log 照样画同一张 onboarding 卡 =
  按钮「点了没反应」;
* 有对话、activity 空 → `messages`(session 里的**对话**消息)非空,把零态
  整个压掉,Activity Log 变成一片没有任何说明的空白。

**修法**:零态改成按 tab 算,且键在 `visibleTimeline`(这个 tab **真正渲染
的行**),而不是「原始流 + session」这对量:
`tabIsEmpty = historyLoaded && visibleTimeline.length === 0`;
`showBootstrapGreeting` / `showEmptyState` 前面加 `!isActivityTab`;新增
`showActivityEmptyState` → `chat.activityEmpty` / `chat.activityEmptyHint`
的 [[bracket|BracketEmptyState]](10 个 locale 全补)。

`bootstrapGreetingPending`(`isBootstrap && loadedByStream.chat &&
historyByStream.chat.length === 0 && messages.length === 0`)**刻意不带
tab 维度**并单独留一个名字:`handleSubmit` 首次发送时靠它把问候语折进
session,而用户完全可能停在 Activity Log tab 上发出第一条消息 —— 渲染条件
可以按 tab 收窄,这条写入条件不能。

顺带(铁律 #8 扫边):同一块 JSX 里「无 agent」的空态硬编码英文
`label="Select an agent"`,而 `chat.selectAgent` / `chat.selectAgentHint`
十个 locale 早就备好了 —— 改回走 i18n。

## 2026-08-26 — 两处 `<MessageBubble agentName={...} />` 删除

[[MessageBubble.tsx]] 移植 `40d353e1` 的「answer 无气泡」改动后 assistant 侧不再渲染头像，
`agentName` prop 整个从 `MessageBubbleProps` 消失。ChatPanel 里 bootstrap 问候气泡和
timeline 里真实消息气泡两处 `agentName={currentAgent?.name || agentId}` 随之删除
（`agentId` 仍保留 — event-log 拉取还要用）。**推翻**下面 2026-08-20 那条「bootstrap 气泡补
agentName 修 AI 头像」的前提：头像本体已经不存在了，这条修复记录仅作历史存档。

## 2026-08-24 — 运行中发送=折进本轮(steer)

`handleSubmit` 不再硬挡 `isLoading`:运行中且 `currentSteerable` 且有文本时,`wsManager.steer` 折进本轮(**return,绝不落到 fresh-run `run(...)`**);非 steerable(或空文本)运行中→no-op(草稿留着,跑完再发)。`clientMsgId` 用 `generateId()`(**不用 `crypto.randomUUID`**——非 secure context http://<ip> 下 undefined 会抛)。`isLoading` 非 loading 时行为逐字不变(仍走 addUserMessage+startStreaming+run)。
**审后订正(#357 fix-first)**:
* **气泡永远上屏 + 反馈闭环**:进 steer 分支后**无条件** `addSteerMessage`('queued'),再看 `steer()` 返回:`true`→清空 composer;`false`(socket 已不 steerable:CONNECTING/CLOSING/closed)→`markSteerRejected(agentId,clientMsgId,'not_sent')` 当场标红,**绝不静默丢弃**(旧稿的"no-op 丢消息"是本轮推翻点)。
* **attachments 运行中不发也不清**:steer 是 v1 纯文本;运行中带附件发送时附件**保留**(不再 `setPendingAttachments([])`),留给跑完后的下一条消息——避免数据丢失。

**审后第二轮(auto-review acff728b 的 N1/N3/R4/M1/M5)**:
* **运行中禁止新增附件(R4 + N1 模式 A)**:`uploadAttachments`(附件上传的**唯一漏斗**,拖拽/粘贴/选文件/录音都过它)开头加 `if (isLoading) { setTranscriptionNotice(t('chat.composer.attachDuringRun')); return; }`。附件键/录音键本就 `isLoading` 禁用,但拖拽/粘贴直达此函数——在**网络上传之前**拦掉,后端不留孤儿文件、界面不出一个「亮着却发不出」的 chip,并给可关提示而非静默吞掉。deps 加 `isLoading`。
* **steer 发送键的宽度契约(N3)**:steerable 运行中并排 Stop + steer 发送键两颗,给 `Composer` 传 `trailingSlots={isStreaming && currentSteerable ? 2 : 1}`,textarea 据此留 `pr-24`(见 [[Composer.tsx]]),否则用户打的字会滑到 steer 键底下被遮。
* **steer 键 title 走 i18n(M1)**:`title={t('chat.steer.sendTitle')}`(不再硬编码英文),与本功能其余文案(placeholder/三态尾标/reason)同口径。
* **测试(R7)**:`chatPanelSteerSubmit.test.tsx` 覆盖 handleSubmit 的 steer 分支——steerable+文本→调 `steer()` 不调 `run()`+乐观气泡 `queued`;`steer()` 返 false→气泡 `rejected`/`not_sent` 且不调 `run()`;非 steerable→无 steer 键、Enter 既不 steer 也不起新 run、草稿不成气泡。mock `@/hooks`(仅覆盖 useAgentWebSocket/useFastMode,其余保真)+`@/lib/api`,用真 store 播种。
* **Composer 保持可编辑**:`disabled={!agentId}`(**不再** `isLoading&&!currentSteerable`)——恪守 [[Composer.tsx]] 的契约「运行中仍可编辑,只 gate 发送」;否则 #355 未合前每次运行输入框都会灰、丢掉「运行中打草稿」既有能力。steerable 运行中显 `steerPlaceholder`(仅真能 steer 时才显示「并入本轮」,不撒谎)。
* **steerable 运行中有发送键**:`isStreaming` 分支渲染 **Stop + (currentSteerable 时) 一颗 steer 发送键**(`right-12`,Stop 在 `right-2`),给鼠标/移动端一个真实提交入口(不只靠 Enter)。该键 `disabled` = `composerEmpty || uploadingCount || !agentId`,**刻意不含 `|| isLoading`**(此刻正流式);fresh-run 那颗发送键保留 `|| isLoading` 作防重复提交,是**另一颗**、各自 disabled。

## 2026-08-21 — 直播块顶部渲染 [[ResumedRunChip]](深圳复测 B1)

渲染条件是 `resumedRun && resumedRun.runId === currentRunId`(#349 M4
收紧,2026-08-24;两个字段都来自 chatStore flat fields):锚点只给
**它自己的** run 打标——将来若出现第二条开流路径,陈旧锚点是隐形而
不是错标到别人的轮次上(为什么这样设计见 [[../../stores/chatStore.ts]]
的 08-24 条)。chip 在直播回复气泡上方,给刷新后重连的全量重放一个
「同一 run 在继续」的身份标识,不再被读成从零重新生成。排查「chip
不显示」时注意 runId 不匹配这条路径。重放渲染路径零改动。
## 2026-08-21 (review) — 两条流各持独立 state,切 tab 不清空/不重取

上一版把 `historyInclude` 塞进 `loadChatHistory` deps + reload effect 清空历史 →
**每次切 tab 都清空重取、滚动跳底**(纯客户端过滤退化成两次网络往返)。改为按 `include` 键控三份
state(`historyByStream` / `loadedByStream` / `totalByStream`)+ 派生出同名 active 变量(`historyMessages`
等)与键控 setter,下游(loadMore/poll/timeline)读法不变。reload 拆两个 effect:agent 变/wipe → 重置双流 +
载入活动流;切 tab → 仅当该流未加载才 fetch(已加载则瞬时、无清空、无滚动重置)。`lastHistoryTimestampRef`
也按 `include` 键控,避免切 tab 后拿另一条流的高水位比较(轮询误判)。

第三轮 review polish:①(N3)reload 与 tab-switch 合并成**单个 loader effect** + `historyIdentityRef`
(`agentId|userId|tick`)——身份变→重置双流+载入活动流;身份未变(切 tab)→未加载才载入,消除挂载时的
双请求。②(N4)轮询 effect 建立时先 `void poll()` 一次,切回已加载 tab 立即刷新而非等满 12s(poll 自带
高水位/`document.hidden` 守卫,无新则空转)。③ tab→流的判定抽到纯函数 `streamForTab`([[chatStreams]]),
三个 fetch 点统一引用、可单测,防漂移。

## 2026-08-21 — 对话/Activity 两个 tab 各取各的流

原来 `loadChatHistory` / `loadMoreHistory` / 轮询都调 `getSimpleChatHistory(agentId, 20)` 拿**一份**
`historyMessages`,`visibleTimeline` 再按 `messageType === 'activity'` 客户端拆两个 tab —— 两个 tab
抢同一个 20 行预算。后端把 A2A/team 活动纳入后这条流变得无上限,繁忙 agent 会把「对话」tab 顶空、
轮询还顶掉已渲染的聊天气泡、把翻旧页的用户拽回底部。改为按 `chatTab` 派生
`historyInclude = chatTab === 'inner' ? 'activity' : 'chat'`,三处 fetch 都带上并进各自 deps —— 切 tab
经 `loadChatHistory` 重建触发 reload effect 自动重取正确的流,每个 tab 独享 `limit`/`offset`/`total_count`。
`visibleTimeline` 的客户端过滤保留:live session `messages` 仍需按 tab 过滤(历史侧已是分好的流)。

## 2026-08-20 — bootstrap 问候气泡传 agentName（修「AI」头像）

`showBootstrapGreeting` 那条静态问候 `<MessageBubble>` 之前没传 `agentName`/`agentId`，
`MessageBubble` 只能 fallback 成通用 `'AI'` 头像（`agentName?.slice(0,2) || 'AI'`）。现在补上
`agentId={agentId}` + `agentName={currentAgent?.name || agentId}`，和 timeline 里真实消息的
`MessageBubble` 调用一致 → 显示 agent 名字缩写。

注：这条静态气泡只在 `historyMessages.length === 0`（首轮之前、还没有 chat 实例）时显示；
用户首次交互后，问候语由后端持久化（`step_1` 开局 seed / `persist_turn` 兜底，见
[[step_1_select_narrative]] / [[_chat_writes]]）成真实消息，走正常 timeline 渲染。发送时压进
session 的那条 client 副本（`Date.now()-1`，无 event_id）靠 `buildTimeline.ts` 的
`(role,content)+5min` 窗口去重 —— 后端 seed 的时间戳锚在 turn 起点满足其中的**时间**条件。
**已知限制**:去重还需 content 相等,而后端落库的是英文默认串、session 副本是
`localizeBootstrapGreeting` 翻译后的串,所以**非英文 UI 下 content 对不上、问候语会渲染两次**。
非本次引入(改前 hook 也写英文 content),根因修复(给 bootstrap 行稳定标识、按身份去重而非 content)
留单独 PR,记在 `reference/self_notebook/todo/`。

## 2026-08-19 — 历史翻页改元素锚定

往上翻页后视图跳到新加载段顶部的根因:`scrollTop = newScrollHeight −
prevScrollHeight` 把 prepend 前后**其他**高度变化(loading 行的出现/消失、
图片落尺寸)也一并算进了差值,且丢掉了原 scrollTop。改用 [[scrollAnchor]]:
prepend 前记住最顶部已渲染条目(`[data-timeline-item]`,React key 稳定所以
DOM 节点跨 prepend 存活)的 rect.top,flushSync 后按该元素实际位移修正
scrollTop——无论位移由什么造成。无旧条目时回退高度差值法。
测试:lib/__tests__/scrollAnchor.test.ts。

## 2026-08-19 — 安全提示可读性修正(更正 08-14 条的「只是位置变了」)

06-17 安全横幅在 v4 里其实同时被降了三件事:warning 底色→全套 ink 最淡的
`--nm-ink30`、`truncate` 截断、完整文案只活在 `title` 悬停里(触屏永远看不到)。
本条修正:去掉 `truncate`、颜色升到 `--nm-ink50`,`title` 保留完整
`chat.securityReminder`(zh-localization.test.ts:62 唯一挂靠点,别删)。
宽度侧改用 `min-w-0` + `line-clamp-2`——两行封顶,窄视口/长译文下不再把
tools row 撑成三行;超出两行仍是省略号,完整文案由 title 承载。
「demote 到 tools row」保留为 Owner 已接受的版式;若要恢复独占横幅需另行拍板。

另:`OnboardingJourney` 挂载点加 `key={agentId}`——它的 `dismissed` 只在挂载时
读一次 localStorage,不带 key 时 React 复用实例,agent A 的关闭状态会漏给
agent B(反向同理,只能刷新页面恢复)。

## 2026-08-06 (2) — lastMessageId

visibleTimeline 里最后一条非 activity 消息的 id,传给 MessageBubble 的
isLatest(meta 行常显);bootstrap greeting 气泡固定 isLatest。

## 2026-08-06 — Chat UI v4:头部重构 + 满铺纸面

- 旧头部(BindingDot + [INTERACTION] + agentId + Sliders + Cost)与
  Conversation/Inner Thoughts 下划线 tab 行、安全提醒横幅,合并为
  [[ChatHeader.tsx]](agent 名主角 + segmented 切换 + Jobs/Inbox/Artifacts/
  Cost 图标 + ⋯ detail 菜单)。移动端保留 md:hidden 的旧式 tab 行
  (桌面头部不在 < md 渲染)。chatTab 状态与 isActivity 路由规则不变。
- 安全提醒文案降为 composer 工具行内的常驻短句(chat.composerPrivacyHint,
  title 悬停给全文)— 仍然永久、不可关闭,只是位置变了。
- 根 Card 去掉 chat-frosted(玻璃层退役,平面 --nm-card);消息流与
  composer 都包在 max-w-[820px] mx-auto 里。
- 气泡改 v4 纸面填色(见 [[MessageBubble.tsx]]);流式 live 气泡的内联
  样式同步改为 paper + hairline + silicon 左描边,nm-bubble-ai 不再用于
  单聊(团队聊天仍在用)。
- sessionLabel:头部 mono 侧标 "会话 · <最近消息时间>"。
- AgentLlmConfigPanel 的入口从头部 Sliders 图标移到 ⋯ 菜单底部。

## 2026-08-14 — chat fast mode: tools row 接入开关

tools row 右侧从单独的 ComposerModelBadge 变为 flex 组：
[[ComposerFastToggle]]（左）+ badge（右）。状态经 [[useFastMode]]
per-agent 持有；handleSubmit 把 fastMode 作为 run() 第 6 参传出。
无 agentId 时开关禁用。

## 2026-08-10 — message submitted at the action boundary

After committing the local user bubble and immediately before opening the run,
the composer records `message_submitted`. Message content and attachments are
never included.

## 2026-07-30 (r2) — 直播回复套 silicon 气泡

直播中的回复原来是头像旁的裸文本，落定瞬间才「长出」蓝底气泡——同一个
东西两副面孔（Owner 反馈）。现在直播回复渲染在与落定后 MessageBubble
完全同款的 silicon 气泡里（silicon-soft 底 + hair 边 + 左 3px silicon
条 + nm-bubble-ai）。仅当已有可见回复内容才渲染气泡壳——只在思考/调工具
阶段不出空蓝框。

## 2026-07-30 — 过程面板挂载 + 直播答案按段渲染

两处改动，同一个分工（过程/答案分离）：

- **composer 上方挂 `ProcessPanel`**（仅 `isStreaming` 时）：Agent 干活
  时过程在面板里滚动，结束即卸载——过程按回复切段折叠回各自气泡
  （`lib/segmentTurn`），所以卸载不丢任何东西。
- **直播中的当前轮**：原来渲染完整 `TurnTimeline`（过程+答案混排），
  现在改为 `SegmentedReply(segmentTurn(currentEvents))` 只出答案。
  过程若也在这里画，会和面板重复一遍。

## 2026-07-23 — day separators in the timeline

Messages show only HH:mm:ss, so multi-day history had no date context.
New `visibleTimeline` memo applies the tab filter BEFORE rendering so
separators compare adjacent VISIBLE items (comparing against
tab-hidden neighbours would draw phantom separators); a separator
(Today / Yesterday / locale date — [[chatDays.ts]]) renders at every
local-day boundary, for both bubbles and activity cards. i18n:
`chat.dateToday` / `chat.dateYesterday`.

## 2026-07-23 — register signal passes focus to upsert

`refreshArtifactFromToolCall` now calls `upsert(d, {focus: true})` so a
successful register_artifact always brings the doc to front, even when a
list refresh raced ahead and already inserted it (see [[artifactStore.ts]]
2026-07-23). Dedup via the module-scope seen-Set is unchanged, so history
re-renders don't re-steal focus.

## 2026-07-22 — localized persistent security reminder

The non-dismissible warning above the conversation now resolves through
`chat.securityReminder` instead of embedding English in JSX. Its persistence,
warning styling, and security meaning are unchanged; only locale selection now
controls the displayed copy.

## 2026-07-21 — localized live execution status

The pre-event streaming indicator no longer hard-codes English for startup,
context/resource loading, workspace preparation, context building, or
thinking. It resolves the whole state-to-copy mapping through
`chat.execution.*`, including the inter-event `Acting…` indicator, so changing
the interface language updates the complete live execution sequence without
altering step identifiers or streaming behavior.

## 2026-07-21 — localized generic bootstrap greeting survives persistence

The generic greeting resolves through `chat.bootstrapGreeting`. Because the
backend persists its generic default in English on the first turn, ChatPanel
recognizes that exact system text in both agent metadata and history and
renders the active-locale version. Scenario-authored `bootstrap_greeting`
metadata remains verbatim, so Arena and other custom greetings are untouched.

## 2026-07-18 — 语音不可用弹窗：free_tier_opted_out → free_tier_not_granted

后端 transcription 枚举改名的前端半边（[[service|transcription/service]]）：
402-录音兜底分支设置的 reason 字符串、弹窗分支判断、以及文案——旧文案第二
条"Re-enable 'Use free quota' in Settings → Quota"指向已删除的开关，整条
删除，只留"加 OpenAI/NetMind key"一条路径。review 二轮：删剩单项的 `<ul>`
铺平成一句话；402 兜底分支的注释("opted out"/"toggle flipped")一并去
开关化。

## 2026-07-15 — pass `actionReason` into MessageBubble props

The `TimelineItem` → `MessageBubble` prop mapping now forwards `actionReason`
alongside `isError`/`warnings`. Without it, a `config_actionable` failure lost
its reason on this last hop and the bubble fell back to the generic "Run
failed" popover instead of the actionable "what you can do" panel. See the
upstream carry in [[buildTimeline.ts]].

## 2026-07-10 — history reload reacts to wipe

The history-load effect now also depends on `chatStore.historyRefreshTick`
(see [[chatStore.ts]]), so a data wipe ([[AgentList.tsx]]) forces an immediate
re-fetch — otherwise the locally-cached `historyMessages` only reloaded on
agent switch and the panel showed stale (already-deleted) messages. The poll
loop can't cover this: it early-returns when the server returns zero messages,
so it never clears a now-empty history on its own.

## 2026-07-09 — hosts the per-agent model/framework panel

ChatPanel now owns the per-agent [[AgentLlmConfigPanel]]: a ⚙ (SlidersHorizontal)
ghost icon button sits in the header's right cluster, LEFT of the cost chip
([[CostPopover]]), and opens it. The panel is rendered once here (state
``agentCfgOpen``); on save it bumps ``modelReloadKey``, passed to
[[ComposerModelBadge]] so the composer's quick-switch chip re-reads the model.
The header icon is the single entry point for the detailed settings — the
composer chip stays only the quick model switch.

## 2026-07-03 — pass error state to MessageBubble

The MessageBubble message prop now forwards item.isError + item.warnings, so
the bubble's error rendering (red badge + red bubble + warnings) actually
receives data. Without this the fields died at the ChatPanel boundary even
after buildTimeline started carrying them.

## 2026-07-03 — Inner Thoughts auto-scrolls to bottom (newest activity)

A dedicated effect (keyed on chatTab + the count of activity items) snaps
scrollContainerRef to the bottom when the Inner Thoughts tab opens and when a
new activity arrives — the tab behaves like a chat log (newest at the bottom,
visible without a manual scroll-down), not an inbox. Reuses the existing
scroll container; the streaming/initial-scroll effects are untouched.

## 2026-07-03 — Inner Thoughts rows are InnerThoughtCard

The activity branch of the timeline map (Inner Thoughts tab, ``messageType
=== 'activity'``) previously rendered a single centred 10px italic line
(content + time). It now renders ``<InnerThoughtCard item agentId />`` — a
source-labelled, expandable card whose expanded region lazily loads the
turn's agent-loop steps by event_id. ``agentId`` comes from the component's
``useConfigStore()`` (already in scope). See InnerThoughtCard.tsx.md.

## 2026-06-25 — two chat tabs: Conversation | Inner Thoughts

A `chatTab` state (`'conversation' | 'inner'`) with a tab bar under the header.
The agent runtime already tags output distinctly, so this is pure frontend
routing — no backend change.

- **Conversation**: the original design, unchanged — full reply bubble + the
  inline "reasoning & tools" disclosure + the live streaming `TurnTimeline`
  (+ "starting up" indicator). It just stops rendering the items that belong to
  Inner Thoughts.
- **Inner Thoughts**: **only** the lightweight background-activity markers —
  `messageType:'activity'` → compact centered line ("Background activity
  (discord)"). Nothing else. No live stream here (post-hoc feed); streaming
  stays in Conversation.

> **2026-06-25 correction.** An earlier version *also* routed any turn whose
> `workingSource` wasn't `chat`/`manyfold` (discord / slack / lark / job / …)
> into Inner Thoughts, on the theory that cross-channel narrations
> (`owner_notify_content`, "I replied to a Discord user / notified you") were
> "the agent's own activity". That was wrong: those narrations are emitted via
> `send_message_to_user_directly` — the agent **deliberately addressed the
> owner**, so they are owner-facing messages and must show in Conversation no
> matter which channel triggered the turn. Routing now keys on `isActivity`
> alone; `workingSource` does not move a message out of the direct
> conversation. `MessageBubble` is unchanged. Routing: `if (chatTab === 'inner'
> ? !isInner : isInner) return null;`.

## 2026-06-20 — design-ref pass: binding-dot header, JourneyBand empty state, Connected footer

Three changes aligning with the Narra Agent App design ref:

- **Header**: the lone `StatusDot` is replaced by [[identity|BindingDot]]
  (carbon·silicon motif) before the `[ Interaction <agent> ]` label; it
  `pulse`s while streaming, keeping the live cue the StatusDot carried.
- **Empty state**: with an agent selected, the generic "Start a conversation"
  bracket is replaced by [[OnboardingJourney]] (binding-dot eyebrow,
  memory→network→team stations, suggested-prompt chips). With NO agent it still
  shows the plain `BracketEmptyState` ("Select an agent"). Note the brand-new
  unnamed-agent path is unchanged — `showBootstrapGreeting` (BOOTSTRAP_GREETING
  "I just woke up" bubble) takes precedence over `showEmptyState`, so the two
  never collide.
- **Composer footer**: briefly carried Enter/Shift+Enter/Drop hints + a
  readiness indicator, but both were **removed** (clawcreek-style minimal
  composer). The send button now uses the `CornerDownLeft` (↵) glyph and a
  `title="Send (Enter)"` so the button itself signals "Enter sends" — no
  separate hint row. (StatusDot/Kbd imports dropped with it.)

Suggested-prompt chips call `composerRef.current.setText(...)` (see
[[Composer]]) — fill, don't send.

## 2026-06-11 (v1.8.1) — clickable Processing chip + header truncation

The Processing indicator is now [[ExecutionPopover]] — click opens a
live pipeline-step list (the execution view retired with RuntimePanel,
resurrected as click-to-peek). Header left side gained
overflow-hidden + agent-id truncation so a narrow chat (artifact
column open) can never run the label under the Processing/cost cluster.

## 2026-06-11 — CostPopover joins the header row

The cost chip used to float `absolute top-2 right-2` over the chat
card (MainLayout) and collided with this header's Processing indicator
during runs. It is now a proper flex member of the header's right
side, next to Processing — no overlap possible. Carries the
`chat.cost` help anchor.

## 2026-05-29 — defer streaming values to throttle render bursts (F5)

The five high-frequency streaming values from chatStore
(currentAssistantMessage / currentThinking / currentSteps /
currentToolCalls / currentEvents) are read into `_rt*` locals then wrapped
in `useDeferredValue` so React coalesces a streaming storm into fewer
commits while always converging to the latest value (iron rule #16:
throttle render rate, never drop/reorder content). `messages` stays
immediate (the timeline dedup depends on it). Pure render-scheduling —
the chatStore delta-merge logic is untouched. Effect (fewer renders under
load) is only observable via real-browser profiling.

## 2026-05-22 — chat input extracted to <Composer> (typing-lag fix)

The message textarea + its draft text used to be `input` state living right
here. Because this component subscribes to the **whole** chat store
(`useChatStore()` with no selector) it re-renders on every streaming delta;
with `input` also here, every keystroke re-rendered this 1300-line monolith,
and typing *while an agent streamed* (esp. one-char-per-token models) made the
two re-render storms collide → laggy input.

The text now lives in `Composer.tsx`. ChatPanel reads it imperatively on send
(`composerRef.getText()`), clears it after a successful send
(`composerRef.clear()`), and tracks only the empty↔non-empty flip
(`composerEmpty`) for the Send button. The drag/paste handlers are passed down
as **stable** wrappers (`stableSubmit`/`stableDrag*` via a ref) so the memoized
Composer doesn't re-render when ChatPanel does. Draft persistence (was a
per-keystroke synchronous localStorage write) is now debounced inside Composer.
`key={agentId}` remounts Composer on agent switch to restore that agent's draft.
铁律 #16: pure render isolation — no message content is dropped or throttled.

## 2026-05-20 — streaming avatar: Bot icon → name-driven RingAvatar

Both in-flight streaming rows (the "events arriving" branch and the
"Starting up…" branch) used to render a hardcoded lucide `<Bot>` icon as the
left avatar — the "old robot" that didn't match the agent's real identity or
the historical `MessageBubble` avatar. Replaced both with
`<RingAvatar species="silicon" label={(currentAgent?.name || agentId || 'AI').slice(0, 2)} />`,
so the live turn shows the same name-initial avatar as finished turns and the
sidebar. Also threads `agentName={currentAgent?.name || agentId}` into
`MessageBubble` (see its mirror md). `Bot` import dropped (now unused).

## 2026-05-15 — artifact card → inline badge

`ArtifactToolCallCards` no longer renders `<ArtifactPreviewCard>` (the full-sized thumbnail with CSV/image/markdown previews). It now emits one `<ArtifactInlineBadge>` chip per **unique** artifact_id in the turn. Re-register on the same artifact is deduped down to a single badge. The card was visually disruptive (re-registers re-mounted it, producing a "flash and disappear" feeling) and the right-side ArtifactColumn is the canonical place to view content — the badge is just an affordance to jump there. ArtifactPreviewCard is kept in `components/artifacts/` for potential future re-use but is no longer mounted from chat.

## 2026-05-15 — re-register signal: refetch (not ensure-loaded)

`ensureArtifactLoaded` (which short-circuited on "already in store") was
replaced with `refreshArtifactFromToolCall(agentId, artifactId, dedupKey)`.
Reason: a `register_artifact` call with `target_artifact_id=<existing>` is
the agent's refresh signal — same `artifact_id` arrives in the tool stream
but with a bumped `updated_at`. The old guard would skip the fetch and
renderers would never see the new timestamp, so the iframe wouldn't
reload. The new helper always refetches, deduped per tool call by a key
built from `tc.step + tc.tool_output` so the render loop doesn't trigger
infinite refetches. The seen-Set is module-scope (small bounded growth
per session, no leak concern).

## 2026-05-14 — artifact tool name collapsed to `register_artifact`

`ARTIFACT_TOOL_BASE_NAMES` is now `['register_artifact']` (was
`['create_artifact', 'upload_artifact_file']`). The frontend's live artifact
discovery keys off this list to recognise tool calls in the agent stream
and surface `ArtifactPreviewCard`s — must stay in lockstep with the
`@mcp.tool(name=...)` registration in `artifact_tool.py`. Also updated the
`ensureArtifactLoaded` helper because `artifactsApi.getDetail` now returns
`Artifact` directly (no `{artifact, versions}` wrapper).

## 2026-05-14 — timeline dedup extracted; event_id-based dedup

The unified-timeline merge + dedup (a ~50-line block inside the `timeline`
`useMemo`) was extracted into the pure, unit-tested
`[[buildTimeline.ts]]` — `buildUnifiedTimeline(historyMessages, messages)`.
The `TimelineItem` type moved there too; ChatPanel imports both.

The dedup itself was upgraded: **`(role, event_id)` exact match** instead
of the old `${role}:${content}` exact-string key. The string key missed
whenever the session-assembled content drifted from the DB-persisted
content by even one whitespace char (two independent code paths) — that
was the "latest reply shown twice" bug. Session messages now carry
`event_id` (stamped by `[[chatStore.ts]]`); see `[[buildTimeline.ts]]`
for the full dedup contract. The old `role:content` + window + consume
logic survives only as the fallback for event-id-less messages.

The "Match-and-consume semantics" / "5 min window" notes in the v2.4
section below still describe the **fallback** path accurately, but the
primary path is now event_id.

## 2026-05-14 — artifact tool-name matching must tolerate the `mcp__…__` prefix

**Bug:** the artifact panel never updated during/after a run — the
artifact only appeared on an unrelated reload (agent switch). Root cause
was here: MCP tools arrive in the event stream **fully-qualified** —
`mcp__<server>__<tool>`, e.g. `mcp__common_tools_module__create_artifact`
— but the code matched a bare-name `Set` exactly
(`ARTIFACT_TOOL_NAMES.has(tc.tool_name)`). That `.has()` never returned
true, so `hasArtifactTools` was always false, `ArtifactToolCallCards`
never rendered, and `ensureArtifactLoaded` never fired.

**Fix:** replaced the exact-match `Set` with `isArtifactToolName()` —
matches the bare name OR a `…__<base>` suffix, so both qualified and
unqualified forms work. `ARTIFACT_TOOL_BASE_NAMES` must stay in sync
with the MCP tool names registered in `common_tools_module` — there is a
reciprocal comment on the tool implementations in `[[artifact_tool.py]]`
flagging this coupling.

(Sibling fix: `tool_output` itself must be clean JSON for the
`JSON.parse` here to work — see `[[output_transfer.py]]`.)

## 2026-05-13 — Phase C: 自动 reconnect 到后端在跑的 run

新增 useEffect 监听 `agentId + userId + currentAgent.active_run.run_id`：
当用户打开（或切换到）一个已经在后端跑着的 agent，前端立刻调
`reconnect(agentId, userId, activeRunId, agentName)`，让 `wsManager`
重开一条带 `run_id` 的 WS。后端识别到 run_id 就走 replay 分支：把
event_stream 里所有 seq ASC 的事件回放完，再 hook 到 broadcaster
拿 live 接续。

业内对这种模式的标准说法（用户在最近一次对话里直接问到）：
**resumable / replayable streaming session**——event_stream 是事件
存储（event sourcing），server-side run 是 long-running operation
(LRO)，WS reconnect = last-event-id-style resumption（W3C SSE 把它
做成 first-class，我们在 WS 上等价实现），整体是 "server-side
session continuity"。

useEffect 的边界条件（顺序 short-circuit）：
1. 没 agentId / userId → 直接返回（panel 还没 ready）
2. `activeRunId` 为 null → 后端没活跃 run，不重连（也是退出条件
   防止 run 结束后死循环）
3. 本地 `isLoading=true` → 当前 tab 自己刚发完 fresh-run 还在跑，
   wsManager 已经管理着一条 WS，**不能**再开一条；reconnect 也
   不需要——本地路径已经在收 live frames
4. 上述都不满足 → fire-and-forget `reconnect()`；`wsManager` 内部
   保证 idempotent（开新连接前 close 旧的）

依赖数组 `[agentId, userId, activeRunId]` 是关键：
- 用户切换 agent：activeRunId 跟着 currentAgent 变化（可能变 null
  或变成新 agent 的 run_id），effect 重跑
- run 结束后 `/api/auth/agents` 下一次拉到 active_run=null，
  activeRunId 变 null，effect 重跑后第 2 步退出 —— **不会**继续
  连旧 run

`reconnect` 故意从 deps 里排除（eslint-disable-next-line）——它在
hook 里是 useCallback 包过的稳定引用，写进去只会徒增噪音。

## 2026-05-11 fix — live activity stays visible after first reply (P0)

The streaming-state UI used to have two mutually exclusive render
branches:

1. `isStreaming && getUserVisibleResponse()` → render a streaming
   `MessageBubble` with the reply content. `thinking` and `toolCalls`
   are passed in but live inside the bubble's **collapsed** Reasoning
   / Tool-calls sections (`MessageBubble.tsx` initialises `showThinking`
   and `showTools` to `false`).
2. `isStreaming && !getUserVisibleResponse()` → render the "Live
   activity preview": italic streaming `currentThinking` text + a
   spinner-decorated list of in-flight `toolSteps`. **Always visible,
   no click required.**

The instant the agent called `send_message_to_user_directly` for the
first time, `getUserVisibleResponse()` flipped from `null` to a
string, branch 2 unmounted, and branch 1 took over. Any subsequent
thinking deltas or tool calls kept accumulating into
`chatStore.currentThinking` / `currentToolCalls` but had **no
always-visible UI surface** — the reply bubble looked finished even
when the agent was still mid-loop running more tools. Xiong's P0
"先回复一条信息后，不再显示思考过程" (`recvjhejbs2abv`).

Fix: drop the `!getUserVisibleResponse()` gate so the live activity
preview now stays mounted for the **entire** streaming window. The
streaming MessageBubble keeps receiving `thinking` / `toolCalls` so a
user who clicks "Reasoning" mid-stream still sees the full trace; the
live preview below provides the always-visible "still working" signal
until `stopStreaming` flips `isStreaming` to false (at which point the
bubble persists into history with its data already attached, line
269-270 of `chatStore.ts`). The `toolSteps` filter (regex
`/^3\.4\.\d+$/` minus `*.send_message_to_user_directly`) intentionally
drops the reply tool call so the same action doesn't appear twice
(once as the bubble, once as a tool step).

Defensive guard: inside the live preview, the `!hasActivity` fallback
now renders the "Starting up..." banner only when
`!getUserVisibleResponse()`. Without this, an LLM that emits no
`agent_thinking` deltas and whose only progress step is the
send_message tool call itself (which `toolSteps` filters out) would
land on `hasActivity=false` *after* a reply already rendered above —
visually contradicting "Starting up..." beneath a populated reply
bubble. With the guard, the live preview cleanly disappears in that
rare path instead.

# ChatPanel.tsx — Unified timeline chat surface with streaming and history pagination

## 为什么存在

The primary user-facing interface. All agent interaction goes through here. Merges two data sources (DB history and live WebSocket session) into a single chronologically ordered `TimelineItem[]` so the user sees one seamless conversation regardless of how many messages have been paginated or how the current run is progressing.

## 上下游关系
- **被谁用**: `MainLayout.ChatView`.
- **依赖谁**: `MessageBubble`, `EmbeddingBanner`, `useChatStore`, `useConfigStore`, `useAgentWebSocket`, `api.getSimpleChatHistory`.

## 设计决策

**Unified timeline**: History messages and session messages are merged and sorted by timestamp. Dedup is done by `role:content` key + 60-second timestamp-proximity check. **Match-and-consume semantics (Bug 19 fix)**: once a session message pairs with a history timestamp, that timestamp is spliced out of the per-key array so it can't dedup another session message with the same role+content. Without consumption, a single history row would gobble multiple session messages — realistic trigger is "user retries the exact same question after a failed turn", which would silently drop the retry bubble from the UI. Plan B — event_id-based precise dedup — is a known future upgrade (author-local todo).

**Polling**: A 12-second interval polls for new background messages (from non-chat agent runs like Jobs). It only replaces the tail of history to avoid losing scroll position for users who've loaded older messages.

**Auto-load when not scrollable**: If the initial history page doesn't fill the container, the panel automatically calls `loadMoreHistory` until the container is scrollable. This prevents the "infinite scroll trigger never fires" problem when messages are small.

**IME handling**: The send button is gated by `isComposing` and a 100ms grace period after `compositionend`. Without this, CJK input methods would fire Enter before the character is committed.

**Bootstrap greeting**: If `bootstrap_active` is true and there are no messages,
the panel renders the localized generic `chat.bootstrapGreeting` when metadata
contains the backend's exact generic English default. Any genuinely authored
custom greeting is rendered verbatim. The same normalization is applied to
persisted assistant history so the greeting does not switch back to English
after the first turn.

**`reply_owner` filtering**: Tool calls with this name are filtered out of the streaming step preview — they produce the main message content, not a tool activity row.

## Gotcha / 边界情况

`flushSync` is used when prepending older messages after "load more" — this forces React to update the DOM synchronously before the scroll position is restored. Without `flushSync`, the scroll restoration would measure the old `scrollHeight`.

The `shouldAutoScrollRef` is the gating mechanism for scroll behavior. User scrolling up disables auto-scroll; new messages re-enable it; streaming start re-enables it.

**Two-mode scroll (Bug 15)**: scroll-to-bottom is split into two effects because "initial open" and "streaming tick" have incompatible requirements. `initialScrollPendingRef` is raised whenever fresh content arrives (initial load, agent switch, background poll, user's own submitted message). A dedicated effect picks it up, waits one `requestAnimationFrame` so `MessageBubble` subtrees (markdown, code blocks, tool-call UI) get a frame to lay out, then snaps `container.scrollTop = container.scrollHeight` — instant, not smooth, and scoped to `scrollContainerRef` only (scrollIntoView on a sentinel would also scroll ancestor containers). The streaming effect uses the classic smooth `scrollIntoView` + sentinel, gated by `isStreaming`, because during streaming the deltas are small and smooth feels right. If you ever need to "jump to bottom" from a new code path, set `initialScrollPendingRef.current = true` — do NOT reach for `scrollIntoView` directly (smooth loses the race against async content layout; that was the Bug 15 root cause).

## v2.4 改动（2026-05-08）— Inline artifact preview cards

- **`ArtifactToolCallCards` component**: a file-local component that receives `toolCalls: AgentToolCall[]`, `agentId`, and `allArtifacts` (pre-read from the store at component scope — not inside the map callback). For each tool call where `tool_name ∈ {create_artifact, upload_artifact_file}` and `tool_output` parses as JSON with an `artifact_id`, it renders an `ArtifactPreviewCard`. While the artifact is not yet in the store, it shows a "Loading artifact…" placeholder and fires `ensureArtifactLoaded` (fire-and-forget fetch → upsert).
- **`ensureArtifactLoaded` helper**: a module-level function (not a hook) that checks `useArtifactStore.getState().artifacts` for the given `artifact_id`. If absent, calls `artifactsApi.getDetail` and upserts the result. Safe to call on every render because the store lookup short-circuits immediately when already cached.
- **Hook rule compliance**: `allArtifacts` is read via `useArtifactStore((s) => s.artifacts)` at the `ChatPanel` component scope (top-level hook call), then passed down as a prop. This avoids calling a hook inside the `timeline.map()` callback.
- **Placement**: `ArtifactToolCallCards` is rendered as a sibling of `MessageBubble` inside each timeline item's wrapper `<div>`, so the cards appear below the message bubble.

## 新人易踩的坑

Custom `bootstrap_greeting` metadata must remain verbatim. Only content equal
to the exact English generic locale value may be localized; the backend stores
that generic greeting as a chat message after the user's first reply.

**Artifact preview placement**: the `ArtifactToolCallCards` render is gated by `hasArtifactTools`, which checks `item.role === 'assistant'`, `agentId` being truthy, and at least one qualifying tool call. This prevents the component from mounting on user messages or when `agentId` is not yet set. The `allArtifacts` dependency means the cards re-render when the store updates (e.g., after `ensureArtifactLoaded` upserts the fetched artifact), replacing the placeholder with the real card automatically.

## 2026-07-21 — voice-unavailable dialog i18n (bug fix)

The voice-input-unavailable `<Dialog>` (title, all three reason-branch bodies + lists, probe
note, Cancel/Open Settings) and the "no longer available" notice were hardcoded English —
they stayed English under a Chinese UI. Moved to `chat.audio.*` keys (en+zh). AudioRecorder
was already i18n'd; only ChatPanel's dialog was missed.

## 2026-08-18 — 字符串匹配降级为徽章锚点(取数寄生拆除)

`refreshArtifactFromToolCall`/`_seenArtifactToolCallIds` 整体删除——渲染循环内
不再发任何请求(静默吞、永久丢失、无限重渲染防护补丁一并消失)。工具名匹配
保留但**仅服务徽章放置**:解析失败最多少一个 chip,tab 永不缺(发现走事件+
全量拉)。徽章本就从 store 读并有占位符,零改动受益。

## 2026-08-18 — 工具改名映射（新增条目；上面带日期的历史条目一律不改写）

本文件上方带日期的条目里出现的是**当时**的工具名，故意保持原样 —— 镜像的价值就在于它记的是
那一天发生了什么，在带日期的条目里改名会让「什么时候变的、从什么变的」不可考。第三轮预审在
23 个文件里查出 68 处这种改写，已全部还原。

现行名字与旧名字的对应：

| 旧 | 新 |
|---|---|
| `send_message_to_user_directly` | `reply_owner`（回答刚说话的 owner）/ `notify_owner`（未被问就主动告知） |
| `bus_send_message` | `message_team` |
| `bus_send_to_agent` | `message_agent` |
| `bus_get_messages` | `read_history`（且改为按会话把手取，不再收 channel_id） |
| `bus_create_channel` | `create_team` |
| `bus_share_to_team` | `team_share_file` |
| `work_add_item` / `work_complete_item` / `work_update_status` … | `team_work_add` / `team_work_complete` / `team_work_update_status` … |
| `ChannelInboxWriter` | `InboxRecorder`（且改写自己的两张表，不再写 bus 表） |

规范解释见 [[chat_module.py]] 与 [[message_source_handler.py]] 的 2026-08-18 条目。

## 2026-09-04 · mounts AgentCapabilitiesPanel (batch 5c)

Opened from the header menu; state `agentCapsOpen`.
