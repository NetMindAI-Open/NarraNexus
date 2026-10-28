---
code_file: src/narranexus/platform/channel/channel_prompts.py
last_verified: 2026-09-04
stub: false
---

## 2026-08-26（下午）— 「尚未生效」的警告改写成准确版本

#359 在源码里留了 `⚠️ THIS SECTION DOES NOT TAKE EFFECT YET`，并写明「加那道
门的人应当在同一个 commit 里删掉它」。那道门（[[step_3_agent_loop.py]] 的
两个前置门）已经加上。

**但没有简单删掉宣布胜利**——因为只接通了一半：

- **agent 对端**：完全接通。选择沉默 = 真的沉默（`agent_peer_no_fallback`）。
  8/14 事故正是这一类。
- **人类 DM**：兜底**仍然会发**，只是有次数上限（`fallback_rate_limited`）。
  这是刻意的：`no_reply_im_dm` 本身就是 0802 的修复本体，为人类也关掉它等于
  把「有人发 hello 却换来沉默」修回去。所以在人类对话上，这一段是**给循环
  设上限**而不是让它停死。

警告因此改写成「这一段到底有多少生效」的精确说明。`test_im_dm_fallback_loop_guard.py`
里有一条断言钉住旧警告不得残留、且新说明必须点名那两个 reason——过时的警告
和过时的承诺一样误导。

## 2026-08-26 — loop-breaker 第三条换成确定版本

PR-1 时刻意写的是「对方的消息**读起来像**机器生成」，并在注释里说明平台
还没有这个信号、有了再换确定版本。信号现在有了
（[[channel_tag.py]] 的 `is_agent_peer`，`format()` 渲染成 `agent sender`），
所以这一条改成**两者都说**：标记在就用标记，不在就仍然靠读。

**两者都保留是必要的**，不是保险起见：只有部分渠道答得出这个问题
（NarraMessenger 从 MXID 读，多数渠道仍然答不出），那些渠道上推测版本就是
全部。

⚠️ 本页 2026-08-25 那条「这一段目前还不生效」**仍然成立**——它指的是
`no_reply_im_dm` 兜底会把沉默改写成一条回复，那道运行时门是另一个 PR 的事。

## 2026-08-25 — DM 协议补 loop-breaker

群聊协议从 2026-03 起就有 loop-breaker（「来回拉锯 = 你在循环里，STOP」）。
DM 协议一直没有——因为它是为**相反**的故障写的（0802 事故：太安静，一句
hello 换来沉默），全文的主张是「回复是默认」。于是在唯一一种**两个 agent
可以单独待着**的房型里，模型被告知必须回复，且没有给出口。8/14 那次两个
agent 在 NarraMessenger DM 里复读了 70+ 小时、6.6 万条消息。

新增 `### Breaking a Loop`，三条：内容重复就停、来回拉锯说一次然后闭嘴、
对面读起来像机器时不适用「人家在等着、会以为我坏了」这条理由。

**口子开得很窄，并显式写一句「这不削弱上面的默认」**——把 0802 的修复保住
是这次改动的最大风险点。`TestDirectProtocolLoopBreaker` 正反两侧都钉：
新段落存在、群聊那套沉默默认**没有**混进来、`pure acknowledgment` 那个
2026-08-06 的窄口子仍在、群聊协议一个字没动。

### ⚠️ 这一段目前还不生效，而且这件事必须写在这里

agent 照这段闭嘴 = 本轮没调回复工具 = 正好命中
[[step_3_agent_loop.py]] 的 `no_reply_im_dm`：helper_llm 会替它写一条回复
并由渠道投递出去。所有 IM 渠道的 1:1 DM 都在这条路上——
`_NO_FALLBACK_WORKING_SOURCES` 只排除了 `message_bus` 和 `job`。

**所以在这一段针对的房型上，今天的净行为变化约等于零**：回复照样发出去，
只是发信人从 agent 主循环换成了兜底槽。prompt 这一半先落地是刻意的（它能
独立 review），但让它真正生效的是运行时那一半——兜底里一个窄的「本轮被判
定为 loop」抑制信号（拆分方案 PR-4）。

这正是本仓反复命中的「文案承诺 > 代码交付」模式，PR#358 的 mirror 里我自己
写过同一句话（「光在 prompt 里允许沉默、运行时不配套，只是把循环从模型搬到
平台」），然后在拆分时把运行时那半分走却没在这里说明。源码注释里也留了同样
的警告，**加上那道门的人应当在同一个 commit 里删掉它**。

### 「重复」不能对人和机器一视同仁（review 抓的最危险一条）

第一版触发器写的是「对方重复了已经发过的内容」——真人最典型的行为就是
第一条没回就再发一遍「在吗」/「?」，正好命中，于是**越是因为没收到回复而
重发，越触发沉默**。那就是 0802 的原始形状，而且更糟。

改成「重复了**你已经答过**的东西」，并显式补一条「如果对方是因为没收到回复
而重发，就回」。第 2 条「我们在绕圈」也补了退出条件（原文「即使还有消息进来
也保持沉默」可能让一次声明永久静音一个 DM，哪怕对方随后提了全新问题）。

`### When You May Stay Silent (narrow)` 末句原本是排他断言（「这就是全部」）
且排在新段**前面**，模型仲裁时可能据此把新例外判为无效——改成「这是**对话还
在往前走时**的全部口子，见下面的 Breaking a Loop」。

三条都钉了断言，而不是再加字符串存在性检查。

**「对面是不是机器」目前交给模型自己读消息判断**——平台还没有这个信号。
信号落地（`ChannelTag.is_agent_peer`）后，这一条会换成确定版本而不是推测
版本。**在信号存在之前不写「被识别为 agent」**：prompt 里留一条永远走不到
的分支，会让下一个人以为这条路已经通了。

拆分背景见 `reference/self_notebook/plans/2026-08-25-ingress-breaker-split.md`。

## 2026-08-21 — 指令 #5 收窄:不再让 agent 手写渠道 reach(PR-2 预审 Important)

reach 现由 [[inbox_recorder.py]] 自动记录,指令 #5 里「→ Store channel contact info under contact_info.channels.{channel_key}」删掉,保留「学到发件人的新信息(姓名/职务/偏好)就调 `extract_entity_info`」并明说「**1:1 才**自动捕获怎么在本渠道触达对方,不用你记」(增量审:限定 1:1,群不自动记,与 [[prompts.py]] §3b 一致)。不删的话两份 prompt 对同一份数据给相反指令,且 LLM 手写会走同一 merge 路径、可能用猜的会话 id **覆盖**自动写对的那条(`rooms[agent_id]` 单值槽)。`{channel_key}` 占位删除后 `.format(channel_key=...)` 仍安全(忽略多余 kwarg)。

## 2026-08-17 — 删掉三处对「怎么回复」的重述（设计 §6.2）

删除：「两个不同的沟通对象」块、回复步骤下的 ⚠️、"Remember" 页脚里的第一条，以及第 6 条
「Owner notification discipline」。

同一条规则此前在**六处**各说一遍（本文件三处、各 trigger 一处、ChatModule instruction、
回复提醒）。六份副本就是六次漂移机会，而且确实漂了：某份副本会继续点名一个该轮桌上已经
没有的工具，agent 拿到两条指令且无从裁决。

规则现在只活在两个地方，且两处都是**生成的而非写死的**：本轮的来源声明
（[[message_source_handler]] 的 `render_origin_declaration`，由 `expressive_tools`
产出的同一个 tuple 渲染）；以及每个 owner 工具自己的 docstring——它随工具一起进上下文，
所以工具不在时它也不可能在。

留下的是**只有这个渠道知道**的东西：房间类型、发送者档案、本平台确切的回复调用方式、
文件/路径投递规则、群聊沉默协议。


## 2026-08-17 — 「禁止承诺」和它隔壁的示例句原本互相打架

新加那条规则的**下一行**，既有的「对话太频繁要收住」bullet 给的标准话术就是
「Let me work on it and share results when ready」——一句承诺未来交付。而这不是主
观判断：[[errand]] 的 `is_promise_only` 里 `\blet me (…|work on)\b` 逐字命中它，
按本项目自己的定义那就是承诺。

模型在「抽象规则」和「带引号的示例」之间通常抄示例，而这条 bullet 触发的场景
（对话过密、该收一收）恰恰最容易脱口而出承诺。示例句改成说清现状、不许诺下一次
交付。

**保住那条 bullet 的出口语义**：它治的是「对话太频繁」，和承诺是两个问题；而且只
说「不要」会让沉默成为合规答案（0802 微信那次的教训）。

IM 群聊**没有工作板兜底**（`record_handoffs` 只在团队房与团队聊天 route 上跑），
所以在这类频道里这段 prompt 就是唯一的机制，它不能自带反例。

`test_the_protocol_does_not_model_the_thing_it_forbids` 用**本仓库自己的判据**
（`is_promise_only`）而不是子串匹配来断言，这样两者不会再各改各的。DIRECT 那条不
动——它没有矛盾邻居，且措辞被 voice 那批用例锁着。

## 2026-08-14 — GROUP 协议补上「禁止承诺未来工作」

这条规则 DIRECT 协议里早就有（「Once your reply is sent, this turn is over」），
GROUP 里一直没有——于是同一句「我稍后回来汇报」在每个群聊频道里都是合规的。

它治的是敦煌形状：模型被人类对话数据训练成「先应答、再干活」，而 runtime 的语
义是「你这条文本 = 你的交付 = 你的终点」。团队房那份同源规则在
[[message_bus_trigger]] 的 `_build_team_prompt` 里（团队房 prompt 自己拼，不走
这两个常量）。

两处都带出口而不是光禁止：只说「不要」会让沉默成为合规答案，那是 0802 微信那
次的失败形状。

## 2026-08-13 — 语音模板：通话上人人有回应

VOICE_REPLY_INSTRUCTION_TEMPLATE 增一条：通话中每句话都要有语音回应——问候/确认/告别也要回短句。动机：DM 协议的「纯确认可沉默」豁免被模型套用到语音告别轮（8/13 实测「拜拜」闷声），通话里的沉默听起来是掉线不是克制。测试锁 test_voice_register。

## 2026-08-06 — voice register 实测硬化（dev 网关真 V4 Flash bench）

裸 register 首版实测两个失败模式：工具后答案漏成 prose（只有进度没结果）、长答案完全绕开 speak。两步硬化后 5 场景 × 多样本全过：①「plain text = 私人笔记，用户永远听不见」的重构式表述（比禁令服从率高）；②「工具完成后必须再 call speak 交付答案」「长答案拆成连续多个短 speak」写成显式规则。实测（经 dev litellm 网关）：TTFT 0.8–1.9s、首段 speak 参数 1.2–3.4s、markdown/URL 零泄漏、预告纪律 100%、长答案 4–6 段口语分段（3/3 一致）。尾部 prose 泄漏在 NexusPower 里是 monologue（桥只消费 speak deltas），用户不可见。reasoning_effort 档位结论待网关侧透传确认后回写（跟踪在 reference/self_notebook/todo/）。

## 2026-08-06 — voice fast mode: RTC 检测 + voice register + speak

新增 VOICE_REPLY_INSTRUCTION_TEMPLATE（channel 无关的 voice register，handoff §7 行为纪律），任何 channel 检测到语音 turn 即可复用。

## 2026-08-06 — 通讯协议按会话类型分叉（1:1 私聊不再吃群聊纪律）

原来 `CHANNEL_MESSAGE_EXECUTION_TEMPLATE` 把**一份**通讯协议注入所有渠道轮次，
`room_type` 只是「Conversation Type」那行的展示值，协议段落根本不读它。于是
1:1 私聊也收到 `Your default action is NO REPLY.` / `When to Reply (rare)` /
群聊纪律 / @mention 纪律。真人私聊发 "hello"，按这套规则**沉默才是正解**——这就是
0802 微信工单。微信是极端案例：它的 builder 把 `room_type` 硬编码成
`ROOM_TYPE_DIRECT`（个人号 v1 只有私聊），所以**每一轮**都命中。

关键论据在下面「设计决策」那段本来就写着：这套协议 2026-03 是为**三个群聊问题**
调优的（agent 间确认循环、群消息唤醒所有成员、@mention 滥用）。套到真人 1:1 上
从来不是设计意图，是连带伤害。

现在的结构：

- `ROOM_TYPE_DIRECT` / `ROOM_TYPE_GROUP` 常量。六个 builder 原先各自手写这两个字面量
  ——那时它只是标签，写错也只是显示问题；**现在它选协议，是真契约**，所以收进常量。
- `COMMUNICATION_PROTOCOL_GROUP` = 原文原样搬出，一字未改。
- `COMMUNICATION_PROTOCOL_DIRECT` = 新写。`Replying is the default.`；沉默口子收窄到
  「对方那条是纯确认（好的/谢谢/收到/👍）且你无新内容可加」；显式列出必须回的情况
  （打招呼、答不全的问题、做不到的请求、闲聊）；**风格规则原样保留**（简洁、一条消息
  一个目的、不表演式汇报）——那些从来和房间人数无关；另加「不许承诺未来的活」，与
  `step_3` 兜底文案 2026-07-30 那两条诚实性规则同源。
- `communication_protocol_for(room_type)`：**只有精确等于 `ROOM_TYPE_DIRECT` 才给私聊版**，
  其余（含 None、大小写不符、未知类型）一律群聊版。不对称是刻意的：在没认出来的房间
  类型里过于安静是可恢复的，往 500 人群里乱发不是。
- 模板里协议段落换成 `{communication_protocol}` 占位符，由
  `ChannelContextBuilderBase.build_prompt()` 按 `room_type` 填。**这是个必填占位符**
  ——手工渲染模板的测试夹具（`test_channel_prompts_path_rules.py`）也得跟着填，
  否则 `.format()` 抛 `KeyError`。

连带修正：Slack 的 builder 原先把 room_type 硬编码 `Group Room`，理由是「DM 和频道
接口一样，简化 prompt 且不损失保真度」——这个理由在 room_type 变成行为开关后失效，
Slack 私聊会继续吃群聊纪律。已改成按 `D...` 频道 id 前缀判定。

测试：`tests/channel/test_dm_communication_protocol.py`（两份协议的内容边界、选择
函数的保守回退、模板占位符接线）。

# channel_prompts.py — 所有 IM 渠道共用的 Prompt 模板库

## 为什么存在

渠道消息 prompt 的结构性文字（"你收到了一条来自 X 的消息"、"发件人档案"、"历史记录"等段落头）在所有渠道间是一样的，变化的只是填入的数据（渠道名、消息体等）。集中管理这些模板有两个好处：调整措辞时一处修改全渠道生效；方便审查和迭代 prompt 效果。

`CHANNEL_MESSAGE_EXECUTION_TEMPLATE` 是最关键的——它定义了整个渠道消息的框架。"通讯协议"章节（规定 Agent 何时回复、何时沉默）自 2026-08-06 起**不在模板里**，而是 `COMMUNICATION_PROTOCOL_GROUP` / `COMMUNICATION_PROTOCOL_DIRECT` 两份，由 `communication_protocol_for(room_type)` 选一份填进 `{communication_protocol}` 占位符。群聊那份是防止 Agent 陷入"自说自话"死循环的核心护栏；私聊那份反过来——防止 Agent 对真人的直接提问装死。

## 上下游关系

**被谁用**：`ChannelContextBuilderBase.build_prompt()` 用 `.format(**info, ...)` 填充 `CHANNEL_MESSAGE_EXECUTION_TEMPLATE`；`_build_sender_profile()` 用 `SENDER_PROFILE_FROM_ENTITY_TEMPLATE` 或 `SENDER_PROFILE_UNKNOWN_TEMPLATE`；`_build_history_section()` 用 `CONVERSATION_HISTORY_TEMPLATE`；`_build_members_section()` 用 `ROOM_MEMBERS_TEMPLATE`。

**无其他依赖**：这个文件只有字符串常量，不导入任何其他模块。

**~~隐式消费者~~（这条已过期，2026-08-06 更正）**：曾经 `narrative/_narrative_impl/continuity.py` 的 `_extract_core_content()` 依赖模板输出以 `[Matrix · ...]` 开头。该函数 **2026-06-01 已删除**——continuity / 叙事检索改用 `build_retrieval_anchor()` 产出的结构化锚点，与执行模板解耦（见 `channel_context_builder_base.py.md` 同日条目）。改模板格式不再有这层耦合风险。

## 设计决策

"通讯协议"章节（"## Communication Protocol"）是 2026-03 经历多轮调优后写入的规则集，解决了三个核心问题：
1. Agent 之间的对话容易陷入"收到→好的→明白了→好的"的无效确认循环
2. 群聊里每条消息都会触发所有成员的 AgentRuntime，但大多数消息不需要每个人回复
3. @mention 被滥用导致每个人都被强制处理不相关消息

这些规则是通用的，不应写入具体 Agent 的 Awareness——Awareness 处理的是"这个 Agent 是做什么的"，通讯纪律是基础设施层面的规范。

## Gotcha / 边界情况

`CHANNEL_MESSAGE_EXECUTION_TEMPLATE` 里的 `{channel_key}` 占位符出现在 Instructions 第 5 条里（`contact_info.channels.{channel_key}`），这是 `get_message_info()` 返回的字段之一。如果子类的 `get_message_info()` 没有返回 `channel_key`，`.format()` 会抛 `KeyError`。

模板里有中英文混合的示例（"好的"、"谢谢"等），这是刻意的——系统主要面向中文用户，给 LLM 提供中文表达的反例让它更好地识别无效确认语。

## 新人易踩的坑

模板里有两个"消息目标"的说明：`matrix_send_message` 回复渠道房间，`notify_owner` 发送给 owner。这两个工具名是硬编码在模板里的。如果渠道的 MCP 工具名改了，必须同步更新这里的说明，否则 Agent 会用错工具。

**File & Path Rules for IM Delivery（Bug 23，2026-04-20 加）**：模板里有一节专门告诉 agent——**IM 对端读不了本地路径**。场景就是 agent 干完活把内容保存成了文件，然后直接回复"保存在 /app/xxx.md 了"。IM 用户看到一条他永远打不开的路径。解法三选一：短内容内联进消息、中长内容创建 Lark 文档发 URL、二进制文件走 Lark 文件上传 API。这和 `basic_info_module/prompts.py` 的 deployment_context 是联动的——后者在 **system prompt** 层提醒 agent "你在容器/本地机里，用户能不能触到你的路径"；这里在 **每条 IM 消息的 runtime prompt** 层重复强调（防止 agent 在长 context 里忘了）。**修改时保持 3 条 delivery route 的结构**，有测试（`tests/channel/test_channel_prompts_path_rules.py`）pin 住。
