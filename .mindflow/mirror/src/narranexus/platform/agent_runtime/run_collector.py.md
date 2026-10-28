---
code_file: src/narranexus/platform/agent_runtime/run_collector.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（PR #394 review 第四轮 I-3）— `RunErrorTracker`：错误结论的唯一规则

把 `collect_run` 里「最后一个错误胜出、但致命粘滞、verdict 类 severity 不被升级」的逻辑抽成
`RunErrorTracker`（`observe(msg)` / `error` / `is_fatal`），`collect_run` 改用它，行为不变；
[[client]] `run_stream` 用同一个对象给流式探测下结论——流式 turn 与收集式 turn 对「失败没有」不会
各说各话。

## 2026-09-03（插件平台批 1）— 事件常量改从 `narranexus.contracts.agent_events` import

`agent_framework/loop/events.py` 已删除（无兼容垫片，铁律 #2），本文件对事件字典常量/构造器的
引用全部指向契约包；语义与线上值不变（`tests/snapshots/golden/agent_events.json` 钉住）。

## 2026-08-31 — 「私密」改口为「永不送达」

宪法在 2026-08-30 改了口径：明文**不再是私密的**，它是用户看得见的工作叙述，
只是**永不送达**。本文件的 docstring 与下方 07-30 那条记录都写着「私密」，
在新口径下是**错的**——错的方向还很危险：它会让下一个读者以为「反正没人看见」，
从而放松对 `include_monologue` 的警惕。

`include_monologue` 的判据一个字没变：**可见 ≠ 已送达，这里守的是送达。**
默认关闭，只有 prompt 已兑现「明文即送达」的调用方（今天 = bus 团队房间）打开。
把可见当成送达的许可，就会把 agent 没对任何人说的斟酌塞进别人的收件箱。

## 2026-08-15 (二) — sink 接缝有测试了

`segments_sink` 之前**零覆盖**：`collect_run` 的测试一次都没传过 sink，而这正是团队房间
现在唯一拿到边界的路径。这种接缝的失效方式是**空**——发帖时 `segments=None`，前端退回单块
渲染，也就是这个特性存在之前的样子：没有报错、没有日志、没有可 grep 的东西。

四条测试分别钉住：run 进行中读得到（deliverer 的位置）、sink 与最终返回不可能分歧、
sink 里是 `{kind, parts}` 而不是契约的 `{kind, text}`（直接读的人必须过 `joined_segments`，
否则每段都会 post 成 None）、以及不传 sink 的那条路径照常返回 segments。

## 2026-08-15 — `segments_sink`：房间要在 run 结束前读到边界

`collect_run` 增加可选的 `segments_sink`：传进来的话，它**就是**累积用的那个列表，所以持有
同一个引用的调用方能在 run 返回之前看到 segments。

为什么需要：#291 之后团队房间的回复**在 turn 内部发出**（chat 行由 `persist_turn` 在
`run()` 返回前写，之后再发的帖子不会被记成一次回复）。而 `RunCollection` 那时还不存在，
`turn.segments` 拿不到。deliverer 被调用时，这一轮回复的 delta 已经全部流过，所以 sink 里
就是它正在发的那段文字的边界。

`joined_segments()` 导出成公共函数：**在途读取**和**最终返回**共用同一个 join，两者不可能
对形状产生分歧，而 `parts` 形态也不会从任何一条路径泄漏出去。

## 2026-08-14 — segment 累积改成 parts，docstring 改成实话

`_add_segment` 原本是 `segments[-1]["text"] += text`。那个字符串同时被 dict 引用着，
CPython 的原地拼接优化用不上，于是**每个 delta 都要复制一遍已积累的全文**——一条长回复
在自己的长度上是二次的。旁边的 `text_parts.append(delta)` + 最后 `"".join(...)` 才是这个
项目已有的正确写法，现在 segments 也走同一条：内部存 `parts` 列表，返回前 join 一次。

这条路径在**每一次** agent run 上，不是团队房间专属；铁律 #14 把几万个 delta 的长跑当成
一等场景，那正是最不该有二次拷贝的地方。

同时把 docstring 的假话改掉：它写着 "Empty unless ``include_monologue``"，而
`AGENT_RESPONSE` 分支根本不看这个开关——每一次私聊、job、Lark 回复都会有一个 `reply` 段。
真话是：**只有 monologue 段**依赖那个开关，所以"`segments` 非空"**不能**用来推断"这是
团队消息"。下一个人会照 docstring 写出一个只在测试里成立的分支。

# run_collector.py — 统一的 AgentRuntime 消息收集器

## 2026-07-30 (二次) — 独白并入改为显式 opt-in（`include_monologue`）

PR #203 review 裁定（fix-first #1）：独白并入 output_text 是全局生效的，但
只有 team room 的 prompt 对 agent 说过「你的明文会被发出去」。peer DM→
收件箱、A2A 这些面上，NexusPower 宪法承诺明文私密——全局并入等于把 agent
以为没人看的内部斟酌公开。**显式选择「收窄」方案**（review 给的二选一）：
`collect_run(include_monologue=False)` 默认关闭，只有 prompt 已兑现「明文
即送达」契约的调用方（今天 = bus 团队房间，trigger 传 `is_team`）打开。
另修 on_progress 相位：带独白的 thinking 帧上报 "response"（agent 在说话）
——但只在 opt-in 的 surface 上（独白私密时没人收到那段文本，上报「回复中」
是假象）；activity 视图不再在产出房间回复时显示 thinking。承重接线
`include_monologue=is_team` 由 tests/message_bus/test_team_monologue_wiring.py
锁定（team owner → True / peer → False），防止重构漏 kwarg 让房间静默哑掉
（#203 事故形态）。
（**2026-08-30 宪法改口为「不投递」**，见 [[library]] / 本文件 08-31 条目；此决定的判据不变。）

## 2026-07-30 — output_text 纳入 NexusPower 独白（群聊 @mention 无回复修复）

`output_text` 的语义从「AGENT_RESPONSE delta 拼接」升级为「agent 说的话」：
AGENT_RESPONSE delta + `AGENT_THINKING.monologue` 子集按到达顺序拼接。
NexusPower 独白契约下 assistant 明文以 thinking 形态流出（monologue 标记），
它就是 claude driver 会走 AGENT_RESPONSE 的那段文本；team room 契约「明文
自动上墙」依赖 output_text，此前拿到空串导致 @mention 回复整段蒸发（dev
evt_238abc4b0b0c4dca：final_output 里躺着完整回复、房间零消息）。provider
CoT 的 monologue 恒为空串，不会把推理泄进 IM 转发。

## 2026-07-30 — 捕获 Step-0 的 event_id

`RunCollection.event_id` + 可选 `on_event_id` 回调：Step 0 完成的
ProgressMessage 已在 `details.event_id` 里携带本 turn 的 events 行 id，
collect_run 在消费循环里抓第一个出现的 id（first-id-wins，最多回调一次），
让 bus 侧（`TurnActivity.note_event_id`）在 turn 还在跑时就能把活动行绑到
events 行——这是团队房间 UI 拉取"刚跑完那轮完整 event_log"的缺失一环，
且完全不触碰 runtime 本身。回调异常吞掉（状态上报绝不能弄坏 run，与
on_progress 同一纪律）。

## 2026-07-27 — 事件类型字面量收敛到 loop/events.py 常量

六种事件形状的字符串字面量改为 import `loop/events.py` 的常量
（TYPE_RAW_RESPONSE_EVENT 等），值逐字节不变——纯机械替换，行为零变化。
事件契约自此有唯一事实源，详见 events.py.md。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 为什么存在

`AgentRuntime.run()` 是一个 async generator，流出 5 种 MessageType
（AGENT_RESPONSE / AGENT_THINKING / TOOL_CALL / PROGRESS / ERROR）。每
个非-WebSocket 消费者（LarkTrigger / JobTrigger / MessageBusTrigger /
ChatTrigger A2A）都要把这些消息汇成一个可返回的结果——文本、工具调用、错误。

在这个文件引入之前，4 个消费者各自复制了同一段 `async for` 循环，且**都
只处理 AGENT_RESPONSE，静默丢弃 ERROR**。这是 Bug 2（Lark 石沉大海）
的直接原因：runtime 明明在传送带上放了 ERROR 消息，Lark 却只听
AGENT_RESPONSE，导致用户看不到任何反馈。

本文件提供一个 `collect_run()` 函数，把"收"集中一处，"展示策略"由每个
trigger 自行决定。新增 trigger（Telegram/Slack/Discord 等）直接调
`collect_run()`，不会再漏 ERROR。

## 上下游关系

**上游 / 消费者**（使用本模块）：
- `module/lark_module/lark_trigger.py` — `_build_and_run_agent`
- `module/job_module/job_trigger.py` — Job 执行主循环
- `message_bus/message_bus_trigger.py` — `_invoke_agent_runtime`
- `module/chat_module/chat_trigger.py` — A2A tasks/send handler

**下游**（本模块调用）：
- `AgentRuntime.run(**kwargs)` — 通过 rebuildable kwargs 转发所有参数
- `schema.runtime_message.MessageType` — 用于 message_type 比对

**不用本模块的地方**：`backend/routes/websocket.py`。WebSocket 不"收"
消息，而是把每条消息流式转发给前端；前端 chatStore.ts 已经正确处理
ERROR → currentErrors。

## 设计决策

**`RunCollection` 是 data-only**。它不做展示决策（展示策略是每个
trigger 的独特逻辑——Lark 发 IM 友好文案，Job 标 failed 状态，
MessageBus 返回结构化错误对象给 sender agent，A2A 写 TaskState.FAILED
消息）。把"收"和"用"分开意味着未来新增 MessageType 只改本文件一次。

**`last error wins`**。多个 ERROR 消息（少见但理论可能）时保留最后一条
——最具体的失败信息。typical case 只有一条 ERROR，行为等价。

**`raw_items` 保留所有原始负载**。LarkTrigger 需要从 TOOL_CALL 事件
的 raw 载荷里抽出 agent 实际发出的 `lark_cli im +messages-send` 文本
（`_extract_lark_reply`）。把 raw 收集成 list 让 Lark 能自己查，其他
trigger 可以忽略。

**`**extra_kwargs` 透传**。`trigger_extra_data`、`job_instance_id`、
`forced_narrative_id`、`pass_mcp_urls`、`cancellation` 等 trigger 特
定参数原样传给 `runtime.run`。collect_run 自身不关心这些参数的语义。

## Gotcha / 边界情况

- **AGENT_RESPONSE 的空 `delta`** 被丢弃（不拼接）。SDK 有时会 emit
  空 delta 作为 keepalive；把空串拼进文本是无用噪音。
- **消息没有 `message_type` 属性** 时整条消息被忽略（不是假设 delta
  字段存在）。这防止 SDK 变更后出现静默破坏。
- **异常不在 `collect_run` 内被捕获**。`AgentRuntime.run` 内部的
  exceptions 会照常向上抛到 trigger 调用处，trigger 可以包 try/except
  做自己的失败兜底。collect_run 只管"正常流完"的情况下的归档。

## 新人易踩的坑

- `RunCollection.is_error` 只读，`@property`。检查错误时用
  `if result.is_error:` 而不是 `if result.error:` —— 后者在空
  dataclass 上也会 truthy 判 False，但语义不直观。
- `RunError` 是 `@dataclass(frozen=True)`，不能就地修改。如果某个
  trigger 想附加自己的上下文（例如 Lark 想加 chat_id），自己包一层
  本地 dataclass，不要改 RunError 实例。
- 和 `format_lark_error_reply` 的关系：那是 Lark-specific 的"怎么把
  RunError 渲染给 IM 用户看"函数，住在 lark_trigger.py。本模块只提供
  RunError 数据结构本身。

## 2026-07-22 — opt-in on_progress callback

`collect_run` gained an optional `on_progress(kind, tool_name)` awaited once per observed
message (kind ∈ thinking|tool|response|error). Opt-in (default None → zero overhead for every
existing trigger); the team bus path uses it to mirror a live activity status
([[_bus_activity]]). Never raises — exceptions are swallowed so status can't break the run.

## 2026-08-13 — `RunError.severity` 与 `is_fatal`:`is_error` 不是"这轮失败了"

`is_error` 由**任何** ERROR 帧置位,包括 `severity="recoverable"` —— 那类帧是 loop
吸收掉、随后照常产出**正确回复**的抖动(provider 429/5xx)。把它读成"这轮失败了"的
后果有两种,都真实发生过:bus lane 把整轮真实回复替换成 ⚠️;team 房间因为通知是单独
贴的,于是**同时**收到正确答案和一条"我处理不了你的消息"。

`is_fatal` 才是"这轮有没有可用输出"的答案。未标注 severity 的错误按 fatal 处理 ——
把可能为空的一轮当成成功,是两个方向里更有害的那个。

**fatal 是粘性的**:收集时 `error` 是"last error wins",对 fatal 不适用 —— 先 fatal
后 recoverable 的一轮仍然没有可用输出,让后来的帧覆盖掉结论会把坏掉的轮次呈现成好的。
所以全程扫到过 fatal,收尾时会把结论压回 fatal —— 但**只压那些知道得更少的后续帧**,
不是无条件的。豁免哪些、为什么,见下面 08-14 那节(它取代本段末句的早期版本)。

## 2026-08-14 — `is_fatal` 之前把两种"其实答了"的情形误判成 fatal

`severity` 有**四种**取值,不是两种:除 `fatal` / `recoverable` 外还有 `recovered`
(fatal 级故障被 helper-LLM fallback 用一条真实回复盖住)和 `recovered_after_reply`
(agent 已经先答过了,故障发生在之后)。初版写的 `!= "recoverable"` 把后两种也算成
fatal —— 而它们恰恰表示**这一轮产出了用户该看到的回复**,判成 fatal 就是把那条回复
换成一句 ⚠️。

现在只有 `fatal` 与未标注算 fatal(未标注取更坏的一侧:把可能为空的一轮当成成功,
是两个方向里更有害的那个)。

**粘性 fatal 同批让路,判据是"谁更知情"。** 只改 `is_fatal` 不够:收集器出口的粘性
步骤会把最后一条 error 的 severity 压回 `"fatal"`,而 `recovered` / `recovered_after_reply`
的**前置条件就是先发生过一次 fatal** —— 粘性必然触发,于是修好的 `is_fatal` 收到的输入
已经被改写成 `"fatal"`,整个修复是空转的。

分界不是"哪些 severity 比较轻",而是**这一帧在说什么**:

* `recoverable` / 未标注 —— 对这一轮的**竞争性描述**,而且比那条 fatal 帧知道得少
  (它不知道前面已经崩过)。粘性挡的就是它们。
* `recovered` / `recovered_after_reply` —— 对那次 fatal 的**裁决**(fallback 答了 /
  agent 先答过)。它们只会**因为**有 fatal 才被发出,把它们上调回 fatal,等于抹掉它们
  存在的唯一意义。

这条知识在同一个文件里一度写了三份(`is_fatal` 的名单、粘性步骤的行内 tuple、`saw_fatal`
只认字面 `"fatal"`),而漂移的表现就是上面这个 bug 本身。现已收成模块级
`VERDICT_ON_FATAL_SEVERITIES`,三处都从它派生。

`saw_fatal` 的置位同批改成"不在 `_NON_FATAL_SEVERITIES` 里就置位",与 `is_fatal` 用同
一份名单;此前它只认字面 `"fatal"`,是同一判据的第三种写法。

**未标注(`""`)在今天的生产路径上不可达**,别照着它设计分支:`ErrorMessage.severity` 是
四值 `Literal` 且默认 `"fatal"`,所以经 `runtime.run()` 流出的 ERROR 帧给不出空串;`""`
只来自手工构造的 `RunError`(`severity` 的默认值,即测试,以及将来某个不走 schema 的
生产者)。上面两处对它的处理是**防御性**的,一律取更坏的一侧。

收尾改写则**豁免 `""`**,理由与豁免裁决帧不同:未标注本来就被读成 fatal,改写它唯一的
效果是抹掉空串携带的那一个信息 —— runtime 没说。

## 2026-08-12 — `segments`：保住独白与回复的边界

team turn 开着 `include_monologue`，于是 agent 自己的思考（`AGENT_THINKING.monologue`）和
它的回答（`AGENT_RESPONSE.delta`）**按到达顺序 append 进同一个列表再 join**——落到墙上的是
一团 markdown，边界没了。

**这就是「复用私聊的 segmentTurn」走不通的原因**：`segmentTurn` 是从**事件流**切的，
而房间消息存在时事件流已经被拍平。前端无法还原，只能猜，而猜错就是把深思渲染成结论。

所以边界在**唯一还持有它的地方**被保存下来。

- `output_text` **一字未改**——它是纯文本消费者（记忆索引、其他 agent 的 scrollback）读的东西，
  一个渲染问题不该改写它。`"".join(s["text"] for s in segments) == output_text` 恒成立。
- 连续同类片段合并：delta 是碎片到达的，不合并的话一个想法会渲染成六个气泡。
- 整轮空白 → 空列表，与上游 `if response_text:` 的丢弃一致，不会复活出一个空气泡。
- **最重要的一条测试是「非 team 逐字节无影响」**：产品里每一个 turn 都走这个函数。
  这条做过变异验证。
