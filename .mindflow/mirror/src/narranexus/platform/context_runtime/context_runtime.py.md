---
code_file: src/narranexus/platform/context_runtime/context_runtime.py
last_verified: 2026-09-22
stub: false
---

## 2026-09-22 - Runtime conversation scopes for MCP permissions

The MCP spec now carries a conversation authorization scope alongside the
existing events-row turn id. It hashes a structured tuple of agent, turn owner,
working source, channel, room, optional channel thread and primary Narrative.
Including owner/source/room prevents a shared Narrative from sharing browser
approvals across users or conversations. Without a Narrative or room, the
scope is restricted to this event; without an event id, it stays absent and
browser tools refuse scoped operations rather than inventing a grant identity.

The digest is token68-safe and does not depend on model-filled tool arguments,
input text, timestamps, transport sessions or the browser's cached session.
It stays stable across turns in the same routed conversation. The standard
header builder sends it both explicitly and in the borrowed bearer, preserving
Claude and Codex transport parity without adapter-specific changes.


## 2026-09-09 — 根回合的 MCP header `root_run_id` 落回自己的 event_id（B-19，#124）

`turn_extra.get("root_run_id")` 只在这个回合的 prompt 是被某条已入树的消息触发时才非空
（bus send 工具在发消息时盖章）。一个**新树的根**——用户在房间里发的第一条消息、一次
job 触发——它的触发者从未盖过章，`turn_extra` 里天生没有这个键，于是旧代码把 header
钉死成 `""`。

这和 `RunRecorder` 写 `events.root_run_id` 的规则（`inherited_root_run_id or run_id`，
见 [[../agent_runtime/run_recorder]]）本该是同一条规则的两个消费点，却只在一处实现：
`events` 表里根回合的 `root_run_id` 一直是**正确的**（等于自己的 event_id），但 MCP
header 传给工具（`record_handoffs` 等）的却是空字符串。工具用空 header 建的工作看板
项就带着 `root_run_id=""` 落库；用户点 stop 时 `backend/routes/runs.py` 从 `events` 行
读到的是正确、非空的 root，拿它去 `pause_by_root(root)` 查询，但查询条件是
`WHERE root_run_id = root`——一个空字符串永远匹配不上这个非空的 root，于是根回合建
的项永远不会被这次 stop 命中，卡在 in_progress 上（跑起来又被 patrol 当成"没人管"
重新派工）。

修法：`event_id` 的计算挪到 `root_run_id` 之前，`root_run_id` 在 `turn_extra` 没有值时
落回 `event_id`——和 `RunRecorder` 完全同一条规则，写在两个必须同步的地方。全仓扫过
其余读 `root_run_id` 的位置（`backend/routes/runs.py` 读 events 列、
`agent_runtime/client.py::_inherited_root_run_id` 喂给 `RunRecorder`、
`message_bus/local_bus.py` 读消息行）——它们都只是转发这条已经正确的值，唯独本文件
这个 MCP header 构造点漏掉了落回自己的一步。

### 次生效果（复审 I5/I6）：级联 stop 的作用域从「job 种下的树」扩大到「所有用户触发的树」

修复前 header 路径其实是个闭环：根回合发 `""` → `bus_messages.root_run_id` NULL → 被唤醒的
run 又把自己标成新根 → 它的发送再是 NULL。非 NULL 树的唯一种子是 `job_trigger.py` 传的
`root_run_id`。修复后每个用户消息触发的回合都是树根，于是一次 stop（`backend/routes/runs.py`
的 `WHERE root_run_id = root AND state = running`）会级联标记整棵树、`local_bus.py` 抑制整棵
树的排队消息、看板项按 root pause——包括**后代**回合建的项，以及**私聊**回合里
`team_work_create` 建的项（本处无条件生效）。这与 `cancel_run` docstring 的树语义
（"stopping only the clicked run leaves those branches burning tokens"）一致，是**有意**的：
用户在单聊里点 stop，这条链路唤醒的其它 agent 的 run 会一起停、排队消息一起静音。
端到端测试 `tests/message_bus/test_cascade_stop_lineage.py::test_a_root_turns_send_puts_the_
woken_run_in_its_tree_and_one_stop_reaches_both` 走真实链路（本文件建 header → 注册的
`message_agent` 工具 → `bus_messages` → trigger 形状的 `trigger_extra_data` →
`client._inherited_root_run_id` → `RunRecorder._bind_run_id` → 真 cancel 路由两跳命中 +
排队抑制），删掉 `or event_id` 即红。

**已知空洞（未修，记录）**：`agent_runtime/client.py::_new_recorder` 在 `recording_enabled()`
为假或 `get_db_client()` 抛错时返回 None，`events.root_run_id` 保持 NULL，而本文件的 header
照样盖章 → `runs.py` 读到 `""` 走单行分支 → 盖了章的看板项又成孤儿。也就是 #124 在「关闭
录制」配置下原样幸存；"header 与 RunRecorder 同规则"在那个配置下不成立，且无断言。

## 2026-09-04（批 3a）— `run(..., context_providers=())`

contextProviders（只在 Assemble 发言的 L2 能力）：稳定段 `contribute_instructions` 在前、易变段
`contribute_turn_context` 在后，按注册序追加到系统提示末尾（`## <name>` 小节）；空串跳过，抛错记 warning。
空集合时字节不变。

## 2026-09-03（批 2a.5）— 插件 MCP server 并入工具面；插件工具的 deferred 名单

`build_input_for_framework` 在模块 server 收集完、排序前调用 `_plugin_tool_surface(module_server_names)`：
`agent.capabilities.mcp_servers` 的站点级 server 加入（模块同名 server 优先），`agent.capabilities.tools` 里
`always_visible=False` 的工具生成全限定名 `mcp__<server>__<tool>` 进 **deferred** 名单（指向未知 server 的
工具丢弃并警告）。返回值多了第五项 `deferred_tools`，随 `ContextRuntimeOutput.deferred_tools` 交给 step_3。
无插件时两者为空，工具面与快照完全不变。

## 2026-08-20 — 回复语言政策反转,详见 [[prompts.py]]

build_reply_language_section 注入的指令改为三级优先:消息内显式语言
要求 > 当次消息语言 > 配置偏好兜底(2026-08-11 条目记的「界面语言
硬约束」当日准确,现已废——正本在 prompts.py 的策略条目,此处只留
指针,不复述)。

## 2026-08-20 — bootstrap 注入的判定外包给共享 lifecycle

Part 5（Bootstrap 注入）里「Bootstrap.md 在不在 + 有没有超阈值」这段判定不再内联,改调
[[../bootstrap/lifecycle]]`.is_bootstrap_active`（返回 `BootstrapStatus`）。原因:`step_1` 的问候语
seed（[[../bootstrap/greeting_seed]]）必须用**同一个** `bootstrap_active` 信号,否则 bootstrap 过期
后 seed 仍会问候。判定归 `bootstrap/` 单一真源,两侧不再各自复算(裸 COUNT 查询也只剩一份)。

**副作用仍留在这里**:`present and not active`（Bootstrap.md 在但超阈值）→ `os.remove` 自动删;
`active` → 注入 `BOOTSTRAP_INJECTION_PROMPT` + 置 `ctx_data.bootstrap_active`。行为与内联版逐路径
等价(helper 返回 `event_count/threshold/bootstrap_path` 让这里能删)。

## 2026-08-18 (review 修正) — 「同一份 prompt 一个时区」这条保证的**适用范围**

上一条注释把它写成了无条件保证，实际只在 relocation 开启时成立（默认开启）。关掉
`PROMPT_TURN_CONTEXT_RELOCATION_ENABLED` 时，`build_complete_system_prompt` 会自己查一次
时区渲染 User Temporal Context，跟 `build_input_for_framework` 的 `turn_tz` 是两次独立查询。

**没有统一**，是因为这两个块在两个先后调用的方法里，唯一能跨过去的办法是 per-turn 缓存——
而缓存会在用户改时区时持有过期值，那比多一次主键查询糟得多。两者能不一致的窗口是：用户在
同一轮的两个 await 之间改了自己的时区。

注释已改成准确的说法并写明了为什么不统一。那个 flag 的定位是 fail-open ops gate（出事会
被真的关掉），所以"关掉之后保证降一档"这件事必须写在纸面上。

## 2026-08-18 (review 修正) — 同一份 prompt 里不能有两个 frame

上一条只修了时间线行。但 `_build_recent_actions_section` 还在用同样的 `[:16]` 裸 UTC 切片，
**和被修好的时间线相隔 45 行、注入同一份 prompt**。

改之前两边都是裸 UTC，彼此一致；只修一边反而**制造了一处原本不存在的分歧**——recent-actions
装的就是 job 执行记录，跟时间线反映的是重叠的事件，现在一个带偏移一个不带，而带偏移的那个
看起来更权威。这正是本次要消灭的那个机制，只修一半比两边都不修更糟。

现在两处共用 `format_timestamp_for_agent`，时区由调用方传入而不是各查一次：两次查询可能
在用户中途改时区时给出不同的值，那就又是两个 frame。

顺带把「一轮只查一次」这句注释变成事实：`turn_tz` 提到 `build_input_for_framework` 顶部，
recent-actions 和时间线循环共用；`_build_turn_context_block` 里的 `block_tz` 同理，
`_build_user_temporal_block` 新增可选 `user_tz` 参数接收它。原来那句注释只在时间线循环内
成立，跨函数其实查了 3 次——注释不准的代价是下一个人会以为有个 per-turn 结果可以复用。

## 2026-08-18 — 时间线时间戳过去是 UTC，而且没标 frame

`_format_timeline_tag` 用 `ts[:16]` 切字符串渲染每条历史消息的时间。`meta_data.timestamp`
的所有写入方都是 `utc_now().isoformat()`，所以切出来的是 **UTC**，而且切完什么标记都不剩。

同一份 prompt 里，「Real World Information」给的是用户本地时间**带 `+08:00` 偏移**。两个
不同 frame 的时间摆在一起，对模型来说读不出"这是两个坐标系"，只读得出"这里有矛盾"，
而它处理矛盾的方式是合理化过去。

对 +08:00 的用户，本地 00:00–08:00 之间发的每条消息都被打上**前一天**的日期。据此解析
「下周五」会整体错位，已经过去的日期会读成还没到 —— 就是线上报的那个现象。

现在时间戳走 `utils/timezone.format_timestamp_for_agent`，转成用户时区并带显式偏移
（`2026-07-30 23:00 +08:00`），跟 ground truth 同一个 frame，可以直接比。偏移放在**每一行**
而不是在导言里声明一次：每行多约 7 个字符，换来模型不需要跨三十行记住一个 frame 声明。

时区一轮只查一次（`_resolve_user_timezone`），逐行查就是 N 次同样的查询。

顺带收掉 `_build_user_temporal_block` 里的第二个 "now"：它渲染的是
`replace(tzinfo=None).isoformat()` —— naive、无偏移、无星期，正是
`format_now_for_agent` 当初为了取代而写的那个格式，还跟正确的那个躺在同一个 turn context
块里。两个 "now" 不可能都是真值，形状错的那个只能增加疑虑。现在 ground truth 只有一个
渲染器、一个落点，这个块只留时区和协议。

## 2026-08-12 — PR #284 review 轮

文案改用 prompts.REPLY_LANGUAGE_SECTION;新段登记进 part_sizes["reply_language"](review #7)。

## 2026-08-11 — reply_language:回复语言偏好落库并注入 system prompt

模块级 `build_reply_language_section(lang)`(纯函数,unset=空串)+ build_input_for_framework 里按 self.user_id 查 UserSettingsRepository 注入「Reply language」段(fail-open)。**字节稳定性**:段内容仅随用户切换语言变化,R4 纪律满足,不打穿缓存前缀;措辞保留 per-message 显式覆盖。

## 2026-08-07 — MCP 身份注入带上 root_run_id

`turn_extra["root_run_id"]` → `agent_id_headers(root_run_id=…)`。与
errand scope 同一个注入点、同一份 turn_extra。作用见 [[_mcp_identity]]:
本轮发出的消息要盖上这棵树,血缘才能过下一跳。

## 2026-08-07 (二次) — event_id 作为构造参数，**追加在 database_client 之后**

`ContextRuntime.__init__` 新增 `event_id`，由 [[step_3_agent_loop.py]] 从 `ctx.event` 传入，
再注入 MCP 身份 header。

⚠️ **踩过一次**：初版把 `event_id` 插在 `database_client` **之前**，于是所有
`ContextRuntime(agent_id, user_id, db)` 的位置参数调用把 db 绑进了 event_id 槽位（18 个测试
当场红）。这正是本仓库 `BEARER_FIELDS` 契约写的那条「**追加，绝不中插**」——同一条规则在
Python 签名上同样成立。

读取端用 `getattr(self, "event_id", None)`：9 处测试用 `ContextRuntime.__new__` 绕过 `__init__`
只设自己关心的属性，裸读会让一个可选字段拖垮无关测试套件。

## 2026-08-07 — 向 MCP 身份 header 注入 team_id

`agent_id_headers(...)` 增加 `team_id=`，取自 `turn_extra["bus_team_id"]`（由
[[message_bus_trigger.py]] 发布）。与既有 agent_id 注入同一动机：工具的 `agent_id` 是模型
填的参数，「我在不在 team」若也靠参数，私聊回合就能声称自己在 team 并写进其工作台。

## 2026-08-04 (review 修正) — team 房中央门控：整轮空 expressive

review Critical #1：只在 bus module 挡 team 房不够——ChatModule 无条件
声明（IM 渠道也会声明），team 轮 expressive 非空 → 两框架的 reminder 在
最贴生成点的位置说「纯文本不送达」，与 team prompt 的「纯文本自动上墙」
对撞。收口移到收集处：`bus_team_room` 标记为真时整轮声明为空（天然命中
claude 适配器 append_reply_reminder 的 no-op 分支与 NexusPower 空契约）。
同批修正：收集点上方的契约注释（原文还写着被推翻的 "must be
priority-driven"）与 3 元组类型标注（实际已是 4 元组）。

## 2026-08-04 — expressive 收集改 origin-first 排序

排序键从 (priority, module_class) 变为 (origin_rank, priority, module_class)：
拥有本轮 working_source 的模块（`claims_source(ws)`，见 [[base]]）
origin_rank=0 排最前。第一个收集到的工具即框架的默认回复工具
（NexusPower constitution 的 example + claude 适配器 reminder 首位），
从此跟着「谁联系的你」走，而不是恒为 priority 1 的 owner-chat 工具——
bus 轮默认 bus_send_message、wechat 轮默认 wechat_send。钩子调用
fail-open（无此方法/抛错 → rank 1，纯 priority 序不变），假模块与
旧路径零影响。

## 2026-08-03 — 注入本轮的**差事作用域**(而不是升级 turn_source)

header 除 turn_source 外再带 `bus_errand_peer` / `bus_errand_channel`
(来源:[[message_bus_trigger]] 分类器判定,经 trigger_extra_data →
`ctx_data.extra_data`);turn_source **保持** working_source 原值。

曾经的错做法(同一 PR 内自我推翻):MESSAGE_BUS + 差事延续时把 turn_source
整轮升级成 `BUS_ERRAND_TURN_SOURCE`。整轮盖章会波及**同一轮里发给其他同伴的
回答**——bus 未读是跨 channel 注入、且模块提示词要求回答的,于是那个同伴把
回答读成提问、不再向自己 owner 回报,P1 换个座位复发(2026-08-03 review
round 4)。所以这里只注入**事实**(我这轮的差事跟谁、在哪个 channel),把
「这一条算不算差事提问」交给知道目标的 send 现场判断(见
[[_message_bus_mcp_tools]] 的 `_send_turn_source`)。

作用域在模块循环外算一次,同轮所有 server 拿同一份;显式
`X-NarraNexus-Errand-*` header 与 bearer 位置字段双通道(codex 只转发
bearer——字段数约定见 [[_mcp_identity]])。

## 2026-08-04 — 盖章处补 user_id（W1，回合属主上同一 seam）

`agent_id_headers(...)` 增传 `user_id=self.user_id`——回合属主与 agent
身份走同一注入点、同一双通道（header + bearer 第 5 位）。`self.user_id`
可能为 None（无属主的触发回合），builder 对 None 省略 header、bearer 尾
字段掉落，服务端回退用模型参数。服务端的弱纪律（占位符才注入、None 不
碰、mismatch 只计量）见 [[_mcp_identity]] 2026-08-04 条。

## 2026-08-04 — 声明收集点 TypeError 单列(review)

fail-open 只该兜"某个模块自己坏了";覆写签名漂移是全站接线 bug,
改为 logger.error 且注明 declaration DROPPED(教训 #3:别把报警吞成
背景噪音——ChatModule 曾因此静默哑掉)。
## 2026-08-03 — `expressive_tools` 增加可选 ctx_data(按来源声明)

回复面声明可按 turn 来源变化——声明面绝不能列出本回合无法投递的死工具
(那是喂给模型的错误信息,弱模型遇声明/指令冲突时常以"写成文字"收场)。
首个消费者:narramessenger 托管回合剔除 trigger 捕获式的 narra_reply,
只声明 narra_send。无 ctx 调用方(测试/旧路径)行为不变。

## 2026-08-01 — mcp_servers spec 注入调用者身份 header

`mcp_servers[name] = {"url": …}` 变成同时带 `headers=agent_id_headers(self.agent_id)`。
这是 P1「Agent 消极回复"我做不了"」的注入侧:模块 MCP Server 由所有 agent
共享,此前工具的 `agent_id` 完全由模型填,填了 `agent_current` 就硬失败。
这一行是**唯一**注入点(两个适配器消费同一个 spec dict);读取与解析在
[[_mcp_identity]]。选 header 不选 URL query 是实测结论——query 在 SSE
传输上会丢。

## 2026-07-31 — 回复契约:投递面由平台声明(expressive seam)

`build_input_for_framework` 在 MCP 收集环里同批收集各模块
`expressive_tools()`(fail-open,姿态同 disallowed),收集后按
**(priority, module_class) 全序排**(R4d 同源,与 _build_turn_context_block /
_sorted_module_instructions 完全一致)再去重——**首位即默认回复工具**且会被
冻进框架稳定前缀,所以顺序必须由优先级驱动、跨回合确定。**不能**依赖
active_instances 原序:那是 created_at DESC 的公共实例序(review 2026-07-31
抓出的 bug:后建的 channel 实例会抢走首位,把 lark_cli 写进 constitution
默认例子,还让新建实例静默打穿全量 cache)。返回值 3 元组 → 4 元组;
`ContextRuntimeOutput.expressive_tools` 承载。

## 2026-07-29 (二次) — 原生 turn 回放(NexusPower)

`build_input_for_framework` 新增 `_load_native_turn_replays`:框架为
NATIVE_REPLAY_FRAMEWORKS(现=nexus_power,同 step_3 的 identity 解析同源)时,当前
narrative 的 assistant 行展开为 [[history_projection]] 从 events.event_log 折回的
assistant/tool 消息序列(独白+工具调用+配对结果),替代两行拍平摘要。user 行保持拍平
(时间线 tag 锚定);跨 narrative 行(`memory_type=short_term`)与无可折 log 的行保持
拍平;窗口外仍由 narrative summary(system prompt Part 2)覆盖——「近期逐字、中期摘要、
远期检索」。逐层 fail-open:回放是增强,任何失败退回拍平行,绝不炸 turn。
`pop()` 防重复行二次注入同一工具序列。

## 2026-07-29 — `build_input_for_framework` 不再拼 timeline 阅读指南

`enhanced_system_prompt` 去掉了 `+ CHAT_HISTORY_TIMELINE_PREAMBLE`；该常量已移到
[[materializer.py]]，与 history 区块同生共死。这里拼的话，argv 预算不够把 30 行
全驱逐后，system prompt 里仍留着一段"下面是你们最近的对话"，模型会去回忆一个不
存在的时间线（prod 2026-07-29）。注意本文件 `run()` 里打的
`System Prompt built: N characters` 是在 preamble / recent-actions 拼接**之前**
的数字——排查提示词尺寸时别把它当最终值。

## 2026-07-28 — R4d：module 块全序排序 + 诊断改按发射序并加前缀分桶哈希

**(1) module 块排序此前不是全序（等长重排型缓存断点）**

`_build_module_instructions_prompt` 原来是 `sorted(key=lambda x: x.priority)`。
`sorted` 是稳定排序，**优先级相同的模块只能继承上游顺序**，而上游是
[[instance_repository.py]] `get_public_instances()`——它此前**没有 order_by**
（同类的 get_by_agent / get_by_agent_and_user / get_chat_instances_by_user 全都有）。
现网真实存在的同优先级并列：BasicInfo(2)/GeneralMemory(2)、
Awareness(3)/SocialNetwork(3)、Lark(6)/Discord(6)/Slack(6)、
Telegram(7)/WeChat(7)。Awareness↔SocialNetwork 一次对调会搬动约 4018 与 4880
字节而**总长度不变**——这是「等长重排」，缓存前缀在第一个换位块处断裂，而所有
按字节数的诊断都报告"没变化"。SQLite 今天恰好返回 rowid 序所以本机没发作；
Postgres/MySQL 不承诺任何顺序（堆表重读、执行计划切换、index-only scan 都会重排），
**这是 cloud 上的定时炸弹，不是理论问题**。

修法（两层都修）：

- 新增 `_sorted_module_instructions()` 静态方法，key = `(priority, name)`，
  成为 module 块顺序的**唯一权威**：发射路径
  （`_build_module_instructions_prompt`）与两个诊断
  （`_log_system_prompt_breakdown` / `_maybe_dump_system_prompt`）全部走它，
  所以**日志打印的顺序 = prompt 拼接的顺序**。`name` 是模块类名，上游已按
  module_class 去重，故 `(priority, name)` 是真全序。
- `_build_turn_context_block` 的 module 块排序同步改成
  `(priority, module_class)` 全序（该块进的是 message 不是前缀，本身不是缓存
  断点，改它是为了"module 块顺序"在全代码库只有一种含义）。元组从
  `(priority, block)` 变成 `(priority, module_class, block)`。
- 数据层：见 [[instance_repository.py]] R4d 条目（`get_public_instances`
  补 `order_by="created_at DESC"`）。

**(2) `[SYSPROMPT-BREAKDOWN]` 此前会主动隐藏等长重排**

`modules:` 段原来按 instruction 长度**降序**打印——两轮之间发生块重排时，
这个列表打印结果完全相同，诊断成了共犯。改为按**发射序**打印
（`_sorted_module_instructions`），重排就表现为 token 换位。

同时新增 `_prefix_bucket_hashes()` + `_PREFIX_BUCKETS`（2000/8000/32000 字符），
在同一行尾部追加 `pfx2k=<6hex> pfx8k=<6hex> pfx32k=<6hex>`：对被测字符串前 N 字符
取 sha256 截 6 位。用途是**定位**——整串的 `ctx_sha256` 只能回答"变了没"，
分桶能回答"变在哪一段"：pfx2k 不同 → 断在前 2K（narrative 元数据区）；
pfx2k 相同而 pfx8k 不同 → 断在 2K–8K（module 块边界区）；以此类推。
不再需要抓包或 dump 才能定位一次等长分歧。

兼容性：`_log_system_prompt_breakdown` 新增**可选** kwarg `prompt_text`
（不传则不输出 pfx 字段，行尾仍以 `ctx_sha256=` 结束），
`total=` / `parts:` / `narrative:` / `modules:` / `ctx_sha256=` 字段与
`[SYSPROMPT-BREAKDOWN]` 前缀全部保持原样，现有日志工具与测试照旧可解析。
调用点在 `build_input_for_framework` 里传 `enhanced_system_prompt`。

**(3) Part 1 注释同步**：narrative 稳定半的字段清单去掉 created_at
（见 [[prompts.py]] R4d：created_at 有两个时钟源，已迁 turn 块）。

测试：`tests/context_runtime/test_module_block_order.py`（随机 20 次洗牌 →
拼接结果恒等；Awareness↔SocialNetwork 对调是等长且无害；turn 块反序输入 →
结果相同；仓储层 order_by 断言）、
`tests/context_runtime/test_system_prompt_breakdown.py`（发射序、三个分桶哈希、
分桶定位一次等长替换、无 prompt_text 时行尾形状不变、dump header 同为发射序）。

## 2026-07-28 — R4c：MCP server 字典确定性排序 + 哈希仪器改标 ctx_sha256

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

- `build_input_for_framework` 收集完 mcp_servers 后 `dict(sorted(...))` 按
  server 名排序（E2 §4：tools 数组跨轮洗牌是第二道缓存断点；本层字典顺序此前
  跟随 active_instances 迭代序）。逐 server 内部工具序 = FastMCP 注册序（代码
  序，确定）；**CLI 侧跨 server 并发连接的合并序仍不可控**，见
  [[adapters/claude/sdk.py]]。codex 两条路径本就 sorted，此改动补齐 claude 路。
  注意 `pass_mcp_servers` 在 StepContext 层 merge（本方法之后），最终排序由
  claude 适配器 `_build_claude_mcp_config` 的 sorted 兜底。
- **仪器校准（E2 §6.3）**：本文件 [SYSPROMPT-BREAKDOWN] 行的哈希改标
  `ctx_sha256=`——它哈希的是 ContextRuntime 层字符串，**不含** claude 适配器
  冷启动轮追加的 `=== Chat History ===` 尾段与逐 system-message 拼接换行，
  不能代表 system[2] 实发字节。权威 `sys_sha256=` 现由 claude 适配器
  post-`assemble_argv_prompt` 发射（[SYSPROMPT-SHA] 行），`grep sys_sha256`
  只会命中真实发送字节的哈希。

## 2026-07-28 — R4a turn-context relocation（system prompt 字节稳定，token 优化三期）

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

**目的**：Anthropic/DeepSeek 前缀缓存是字节/块级的，system prompt 里任何每轮易变
字节（Part 0 temporal 秒级时间戳、Part 1 narrative 的 updated_at/current_summary、
尾部 recent_actions）都会打穿缓存。R4a 把这些**非模块**易变段整体搬到**当前轮
user message 前部**的 `[Turn context]` 块（照 2026-07-09 附件 marker 先例：只改
LLM-facing 的 current_user_content，**绝不动 `ctx_data.input_content`** → 聊天持久
化/前端/冷启动历史重合成零感知）。只搬不删（铁律 #16）：模型每轮照样看到全部内容。

- 开关 `settings.prompt_turn_context_relocation_enabled`（默认 true；env
  `PROMPT_TURN_CONTEXT_RELOCATION_ENABLED`）。**关 = 装配与 R4 之前逐字节一致**
  （temporal/narrative 完整模板/recent_actions 全部回原位）——fail-open 运维闸门。
- `build_complete_system_prompt`：开关开时跳过 Part 0、Part 1 用稳定版模板
  （`combine_main_narrative_prompt(include_volatile=False)`，见
  [[prompt_builder.py]] 模板拆分）。
- 新增 `_build_turn_context_block(active_instances, ctx_data, narrative_list)`：
  固定顺序 temporal（块名 "User Temporal Context" 不变，job MCP docstring 引用它）
  → narrative turn 块 → 模块 `contribute_turn_context` 块（module_class 去重、priority
  升序稳定排序，与 `_build_module_instructions_prompt` 同语义）→ recent_actions。
  逐 part fail-open（warning + 跳过，不打死轮次）。R4a 阶段无模块 override
  （R4b 才逐模块搬），模块块为空。
  **所有 part 都为空时返回 `""` 而非只剩 header**，调用点同样跳过包裹 ——
  否则会给用户原话前缀两个空行加一个"分隔了个寂寞"的 `--- User message ---`，
  并让模型去找一个不存在的小节。这条路径只在 temporal 失败（唯一常驻 part）
  且无 narrative / 无模块块 / 无 recent_actions 时可达。
- `build_input_for_framework` 新增 kw 参数 `narrative_list`（run() 传入；None =
  无 narrative turn 块）；`[Turn context]` + `--- User message ---` separator 前置
  拼接在附件 marker 逻辑**之前**。
- **[SYSPROMPT-BREAKDOWN] 发射点从 build_complete_system_prompt 移到
  build_input_for_framework**（哈希"最终送适配器的 system prompt 字符串"，度量什么
  就哈希什么）：行尾追加 `sys_sha256=<sha256(enhanced_system_prompt)[:12]>`，
  parts 增加 `turn_context=<chars>`；`total=` 语义变为 enhanced（含 preamble）长度。
  breakdown 输入经 `self._last_part_sizes / _last_module_instructions /
  _last_narrative_meta` 在两方法间传递（ContextRuntime 每轮一个实例，不跨轮泄漏）。
  两轮 grep sys_sha256 相同 = 前缀稳定哨兵（R4b 收尾后才翻转为相同——Anthropic
  all-or-nothing，R4a 单独合入时 BasicInfo 等模块仍易变属预期）。
- history 与 system prompt 的关系更新：历史仍走 unified timeline role messages
  （不变）；**当前轮消息现在 = [Turn context] 块 + separator + 用户原话 (+ 附件
  marker)**，claude/codex 适配器 `messages.pop()` 原样取走，零适配器改动（铁律 #9）。

Plan：`reference/self_notebook/plans/2026-07-25-r4-prompt-stability.plan.md`（R4a）。
Tests：`tests/context_runtime/test_turn_context_relocation.py`（开关关闭时恢复
pre-R4 **小节位置** / 装配顺序 / 模块收集 fail-open / sys_sha256 稳定性 /
空 turn context 不发 header）、`test_temporal_context.py`（建造点迁移）。

**用词更正（2026-07-29，PR #185 review）**：早期注释与文档写的"开关关闭 =
与 pre-R4 字节相同"是夸大。关闭开关恢复的是**小节位置**，不是字节流 ——
三条 determinism normalisation（narrative 时间戳规范化、模块块 (priority, name)
全序、mcp_servers 排序）是无条件生效的，它们不搬运也不丢弃任何内容，但确实改字节。
module 级的"这段模板没动"仍然是准确的窄声明，只有 assembly 级的说法被改掉。

## 2026-07-24 — `build_input_for_framework` 新增第三返回值 `disallowed_tools`（B++）

返回值新增第三项：汇总各模块 `disallowed_tools()`（见 [[base.py]] 通用面 /
[[channel_module_base]] channel 覆写）的全限定工具名列表，排序去重。单模块收集
失败 **fail-open**（warning + 跳过——宁可多花 token 也不误伤已绑定 channel）。
用途：未绑定 channel 的工具 schema 不进模型上下文，经 [[context_schema.py]]
`ContextRuntimeOutput.disallowed_tools` → [[step_3_agent_loop.py]] → driver
kwargs 下传。Plan：token 优化 W2 B++。

## 2026-07-15 — `build_input_for_framework` 返回 MCP spec dict

返回值第二项从 `{name: url}` 改为 `{name: {"url": url}}`（模块内部 MCP 无
headers）；用户外部 MCP 的 headers 由 backend 装配层（websocket/skills）注入
`pass_mcp_servers`。命名统一 `mcp_servers`。

## 2026-07-14 — [SYSPROMPT-BREAKDOWN] 诊断日志（system-prompt-growth 事故取证）

`build_complete_system_prompt` 现在在 return 前打一条 INFO
`[SYSPROMPT-BREAKDOWN] agent=… total=… | parts: security/temporal/narrative/modules/bootstrap=各字节 | narrative: nar_summary_chars/nar_dynamic_entries | top_modules: 最大 5 个模块指令`。
纯诊断、不改行为。**动机**：观测到 system prompt 逐轮增长(app ~100k、dev ~115k 上限
`MAX_SYSTEM_PROMPT_LENGTH`),逼近上限后历史被驱逐、agent(含原生 opus)停止调
`send_message_to_user_directly`。此前每 Part 字节只在 `logger.debug`(生产 INFO 级看不到)。
新增纯静态 helper `_log_system_prompt_breakdown`(可单测,见
`tests/context_runtime/test_system_prompt_breakdown.py`)。narrative 的 `current_summary`
字节 + `dynamic_summary` 条数是增长头号嫌疑,单独打出来量化。

## 2026-07-10 — 移除写死的假模型身份（改由 BasicInfoModule 动态填）

`run()` 构造 `ContextData` 时曾写死 `agent_info_model_type="Claude Agent SDK"` +
`model_name="sonnet-4"`，经 basic_info [[prompts.py]] 的 "LLM Model" 段灌进系统
prompt → **每个** agent（含 codex_cli+gpt5）都自称 Claude Sonnet-4，被问模型就照读
（违反铁律#9）。两行 kwargs 已删；这两个字段改由 [[basic_info_module.py]]
`gather` 经 [[providers/model_identity.py]] 按真实 slot 动态填。
ContextRuntime 从此不掺和模型身份（本就不该知道），字段也在 [[context_schema.py]]
正式声明了。

## 2026-07-09 — current-turn attachment marker injection

`build_input_for_framework` 追加"当前 turn user message"时，读 `ctx_data.extra_data["attachments"]`，通过 `Attachment.markers_from_dicts(agent_id=ctx_data.agent_id, user_id=ctx_data.user_id)` 合成 marker 拼在 LLM 视图的 content 尾部。**关键：不动 `ctx_data.input_content`**——那个字符串会被 `ChatModule.persist_turn` 原样写成用户消息的 `content`，`backend/routes/agents/chat_history.py` 又会把它回显到前端。marker 只走 LLM 视图，绝对路径不进 UI 也不进 DB。

`ctx_data.user_id` 已被 `AgentRuntime` 覆写为 agent owner（`_agent.created_by`，agent_runtime.py:245），marker 里的路径拿到的就是 owner workspace 的绝对路径——跟 trigger 落盘时的路径一致，agent Read 直接命中。

覆盖范围：所有 IM 渠道 + WS 前端 chat 都已把 `attachments` 放进 `trigger_extra_data`（channel_trigger_base.py 的 `_build_and_run_agent` line 1175-1178；backend/routes/websocket.py line 679），零 trigger 侧改动。下一轮读历史时 `ChatModule._synthesize_attachment_markers`（同一 `Attachment.markers_from_dicts` 底层）再合成一次，marker 格式两条路径完全一致，agent 行为在当前 turn vs 历史 turn 上无差别。

Regression：`tests/channel/test_current_turn_attachment_marker.py` 5 条锁死"注入不动 input_content / owner routing / malformed 有 WARNING / 空列表零动作"。

## 2026-06-17 — system prompt 第一段注入安全铁律(**云端专属**)

`build_complete_system_prompt` 在所有其它段(temporal / narrative /
module / bootstrap)之前 append `prompts.SECURITY_IRON_RULES`,确保没有后续
段落或用户消息能覆盖它。**仅当 `get_deployment_mode()=="cloud"` 时注入** ——
铁律是多租户保护;本地/桌面是用户自己的机器,用户就是要 agent 跨自己的文件夹
干活,注入它会废掉本地体验,且本地没有别的租户/平台密钥要保护。详见
`prompts.py.md`。

## 2026-06-12 — User Identity Context block REMOVED (治本: moved into basic_info)

The `_build_user_identity_block` method, its "Part 0b: User Identity" injection
in `build_complete_system_prompt`, and the `USER_IDENTITY_CONTEXT` import are
all gone. That block was a redundant second place to inject owner/sender
identity — the canonical identity injection lives in [[basic_info_module.py]]
(`gather` + basic_info `prompts.py`), which is where the human-name
fix now lives. Removing it avoids two competing identity sources in the system
prompt. See the 2026-06-11 entry below for what the now-deleted block did.

## 2026-06-11 — User Identity Context block (owner + sender, by human name)

build_complete_system_prompt now injects a "Part 0b: User Identity" block via new `_build_user_identity_block(ctx_data)`: states the agent OWNER by display_name (NetMind nickname / local display_name; falls back to user_id, never shown as a name otherwise), and — when the trigger carries `sender_user_id` in extra_data (only chat does) — whether the current sender is the owner or a visitor (resolves their display_name, compares to owner). IM triggers don't set sender_user_id (their own module trust block handles sender), so they get only the owner line; job/bus likewise. Cleanly separates user_id (opaque scoping key) from the human name. Defensive: lookup failure never breaks the prompt.


## 2026-05-29 — EverMemOS removed

The "Relevant Memory" prompt section is gone. `build_complete_system_prompt`
and `run()` no longer take `relevant_episodes`; `_build_relevant_memory_prompt`
was deleted; `_build_auxiliary_narratives_prompt` no longer takes
`evermemos_memories` (it now renders only the auxiliary narrative summaries).
System prompt is now: temporal context → main narrative → module instructions
→ bootstrap. Long-term memory is the current narrative's full history, surfaced
by [[chat_module.py]] as the unified timeline (see note below).

## 2026-05-20 (Fix #2 P1) — render the unified timeline; drop the cross-narrative system-prompt section

`build_input_for_framework` no longer splits chat_history into long/short and
no longer injects cross-narrative memory as a separate system-prompt section
(via `_build_short_term_memory_prompt` + `SHORT_TERM_MEMORY_HEADER` — now
DEPRECATED/unused). It renders the single unified timeline (built by
[[chat_module.py]]) as real role messages, each prefixed by
`_format_timeline_tag()` → `[time · topic · nar_id]` plus the channel source
prefix, and prepends `CHAT_HISTORY_TIMELINE_PREAMBLE` to the system prompt to
teach the agent how to read it (tags, how it was assembled, and what the user
can/can't see — reasoning is private). `[CHAT-CTX] unified timeline rendered`
log line reports total / cross / current counts. `_format_timeline_tag` now also
emits `evt=<event_id>` per line (for view_event drill-down).

## 2026-05-20 (Fix #2 P2) — recent-actions section in the system prompt

`_build_recent_actions_section` renders `ctx_data.extra_data['recent_actions']`
(populated by [[chat_module.py]] `_load_recent_actions`) as a compact
`RECENT_ACTIONS_HEADER` block appended to the system prompt — one line per
background activity `- [time] <source>: <job title / summary> (evt=<id>)`. Kept
separate from the conversation timeline so background work doesn't pollute it.

## 2026-05-19 — `_source` carried on final_messages

`build_input_for_framework()` now stamps each long-term history row with
an internal `_source` field copied from its `meta_data.working_source`
(default `"chat"`). Consumed by [[adapters/claude/sdk.py]] for
source-aware truncation: when the system prompt + history would exceed
the SDK's argv ceiling, oldest background-trigger rows
(`job / message_bus / lark / callback`) are evicted first; chat rows
are kept until the budget can't be met any other way. Other SDK
adapters (OpenAI Agents, Gemini) build their own message dicts so this
extra key never reaches them.

# context_runtime.py — the assembly engine that turns raw Narrative + Module state into a ready-to-submit LLM payload

## 为什么存在

Before each LLM call, the agent needs a fully formed system prompt and a message list. That assembly is non-trivial: it requires pulling the right Narrative summary, firing every active module's data-gathering hook, sorting module instructions by priority, routing conversation history into two memory tracks (long-term vs. short-term), truncating oversized messages, and collecting MCP server URLs for tool access — all in a deterministic order. `ContextRuntime` owns that entire assembly pipeline so the orchestration layer (`step_3_agent_loop.py`) can hand it a Narrative list and an instance list and receive back a `ContextRuntimeOutput` without knowing anything about how the prompt was built.

Without this class, the assembly logic would bleed into `AgentRuntime` steps, each module would need to know about every other module's output format, and the prompt structure would become impossible to reason about or test in isolation.

## 上下游关系

**Receives from:**
- `step_3_agent_loop.py` (inside `agent_runtime/_agent_runtime_steps/`) is the exclusive runtime caller. It constructs a `ContextRuntime` instance with the `agent_id`, `user_id`, and a `DatabaseClient`, then calls `.run()` with the Narrative list and active module instances produced by earlier pipeline steps.
- `NarrativeService` (`narrative/`) — called inside `build_complete_system_prompt()` to format the main Narrative's summary prompt via `combine_main_narrative_prompt()`.
- `HookManager` (`module/hook_manager.py`) — invoked in `run()` Step 1-2 to fire `gather` on every loaded module, which allows modules like `ChatModule` to populate `ctx_data.chat_history`.
- `AgentRepository` (`repository/`) — queried directly inside the Bootstrap injection block to look up who created the agent, bypassing `BasicInfoModule` to avoid a module-load dependency. **Bootstrap deletion is now profile-driven (2026-06-16)**: the auto-delete threshold is no longer a hard-coded `>= 3` — it comes from `bootstrap.profiles.auto_delete_threshold_from_meta(agent_record.agent_metadata)` (missing key → historical default 3; `None` → never rule-delete, semantic-only). The injection prompt stays the global `BOOTSTRAP_INJECTION_PROMPT`.
- `prompts.py` — all section header strings are imported from the sibling file.
- `schema` (`ContextData`, `ModuleInstructions`, `ContextRuntimeOutput`, `WorkingSource`) — provides the typed containers that flow through the pipeline.

**Consumed by:**
- `step_3_agent_loop.py` — the only caller that constructs and runs `ContextRuntime`. Its output (`ContextRuntimeOutput.messages`, `ContextRuntimeOutput.mcp_urls`, `ContextRuntimeOutput.ctx_data`) is forwarded to the agent framework adapter in subsequent pipeline steps.
- The package's `__init__.py` re-exports `ContextRuntime` under `narranexus.platform.context_runtime`, but no other module within the package imports it at runtime.

## 设计决策

**Chat history comes from `ChatModule`, not from Event records.** The original design stored conversation turns as `Event` objects and reconstructed the message list from them during context assembly. After the 2025-12-09 refactoring, `ChatModule` (via `EventMemoryModule`) provides `ctx_data.chat_history` directly. The old `extract_narrative_data()` method and the Event History section of `build_complete_system_prompt()` are both commented out rather than deleted — they remain as documented fallbacks while the new approach is validated. This means there are dead code blocks with explicit `TODO` annotations; they are intentional placeholders, not forgotten debris.

**Dual-track memory split inside `build_input_for_framework()`.** Each message in `chat_history` carries a `meta_data.memory_type` tag set by `ChatModule`. Messages tagged `long_term` are placed as ordinary `role/content` pairs in the messages list (chronologically ordered, per-message truncation applied). Messages tagged `short_term` are serialised into the system prompt via `_build_short_term_memory_prompt()` under a dedicated markdown section. This separation exists because the LLM's context window treats the system prompt differently from the message history — short-term cross-topic context is better positioned as background framing than as fake conversation turns.

**Module instructions are deduplicated by `module_class`, not by `instance_id`.** A single module type (e.g., `JobModule`) can have multiple instances (one per job). If each instance contributed its own instructions section the system prompt would contain near-identical paragraphs. Deduplication at the `module_class` level ensures each module type contributes exactly one instruction block, taking its wording from whichever instance is seen first during iteration.

**Bootstrap injection is self-destructing.** The `Bootstrap.md` file is written once by the agent creator to seed initial behaviour. After three Event records exist for the agent, `context_runtime.py` deletes `Bootstrap.md` automatically on the next run. The threshold of three events is a deliberate grace period — the first few turns often include the bootstrap instructions being read and acted upon. If the agent fails to delete the file itself, the auto-delete prevents perpetual bootstrap mode without requiring external cleanup.

**`SINGLE_MESSAGE_MAX_CHARS = 4000`** is a per-message safety cap only. Overall context length management is delegated to the Claude Agent SDK's `MAX_HISTORY_LENGTH` setting. The two limits address different failure modes: per-message truncation prevents a single large paste from dominating the context window, while the SDK's history limit prevents total token overflow across many turns.

**`SHORT_TERM_TOKEN_LIMIT = 40_000` characters (≈ 10k tokens).** Short-term memory is intentionally given a smaller budget than the main message history. Groups are processed in reverse chronological order so the most recent cross-topic context survives budget exhaustion.

## Gotcha / 边界情况

**`run()` always appends the current user input as the final message.** The current turn's `input_content` (from `ctx_data`) is appended to `final_messages` after all history is inserted. If a caller accidentally includes the current turn in the `chat_history` they pass to `ContextRuntime`, the LLM will see it twice — once in the history position and once as the trailing user message. `ChatModule` is responsible for ensuring `chat_history` contains only prior turns.

**Auxiliary Narrative summaries are computed twice if `extract_narrative_data()` is disabled.** The commented-out `extract_narrative_data()` call would have populated `ctx_data.extra_data["auxiliary_narratives"]`. Because it is disabled, `build_complete_system_prompt()` has a fallback that extracts the same summaries directly from `narrative_list[1:]`. Any change to the auxiliary Narrative summary format must be applied in both places (the fallback block and the `extract_narrative_data()` method body), otherwise the two paths will diverge when `extract_narrative_data()` is eventually re-enabled.

**`evermemos_memories` enriches auxiliary Narrative summaries.** If the orchestrator layer passes `evermemos_memories` into `run()`, it gets injected into `ctx_data.extra_data` and later consumed inside `_build_auxiliary_narratives_prompt()` to append "Related Content" snippets. If `evermemos_memories` is `None` (the default), the section appears without enrichment and no error is raised. The enrichment path is Phase 3 functionality; leaving it `None` is the safe default.

**Bootstrap detection performs a raw SQL `COUNT(*)` query.** The Bootstrap injection block bypasses the Repository layer and issues `db.execute("SELECT COUNT(*) AS cnt FROM events WHERE agent_id = %s", ...)` directly. This is intentional to avoid pulling in `EventRepository` as a dependency, but it means the query is not covered by the standard repository test harness and will silently return `event_count = 0` if the query fails, which keeps the bootstrap prompt active longer than intended.

## 新人易踩的坑

The `run()` method's Step 1-1 comment says "Event selection disabled" and sets `messages = []`. This is not a bug — it is a documented transitional state. Do not "fix" it by restoring `extract_narrative_data()` without understanding that `ChatModule.gather()` in Step 1-2 is now the authoritative source of conversation history. Enabling both simultaneously would produce duplicate message history.

`ContextRuntime.__init__()` accepts a `database_client` parameter but falls back to `get_db_client_sync()` if none is provided. In test environments where no database is available, omitting this parameter produces a `DatabaseClient` that fails on the first `await` rather than at construction time — the same lazy-init gotcha documented in `database.py`.

## 2026-08-18 — 收集环把 `ctx_data` 传给 `disallowed_tools`

同环内 **先压制、后声明**，此顺序此前是隐式契约。压制 hook 不带 ctx，需要按轮次决策的模块
只能读声明 hook 遗留的实例状态 —— 在这个顺序下永远是空的（详见 [[base.py]] 2026-08-18）。
现在两个 hook 同传 ctx_data，顺序不再有语义。fail-open 姿态不变。

## 2026-08-18 (二) — 压制 hook 也要有 TypeError 响亮分支

声明侧 `expressive_tools` 早有 `except TypeError → logger.error`，起因是一次签名漂移
静默清空了 ChatModule 的整个声明面。压制 hook 的 `ctx_data` 参数是 2026-08-18 才长出来的，
于是它正处在同样的位置上 —— 而后果更重：压制 fail-open 会让**两个**发送动词都留在桌上，
在 patrol 轮上就是一张自己的提示明令禁止的桌子（即 C1 那一类缺陷复现，藏在没人 grep 的
warning 后面）。`test_every_module_disallow_signature_accepts_ctx_data` 只覆盖 module_registry，
这条分支覆盖它看不到的情况：从不进入 map 的模块类。

## 2026-08-18 (三) — TypeError 分两种：签名被拒 vs 函数体抛出

两个响亮分支（声明面与压制面）都无法区分「调用被签名拒了」和「实现体自己抛了 TypeError」。
两者都 fail open、行为一致，所以代价纯在日志上 —— 而那不是小事：on-call 读到「signature
mismatch」、去查覆写签名、发现签名没问题，于是手上多了一行与代码矛盾的 ERROR，而真正的后果
（压制被丢掉、patrol 桌上留着它自己提示禁止的两个动词）没人去看。

判据是 traceback 深度：签名 TypeError 在绑定参数时抛出、从不进入被调方，所以只有一帧；函数体
里抛的至少两帧。预审执行验证过误报：一个签名正确、体内做 `["a"] * None` 的模块被报成了
signature mismatch。

两个分支同批改 —— 只改一个会让另一个的文案在对比之下更具误导性。fail-open 姿态不变，只改
消息与是否 `logger.exception`。

## 2026-09-04 · one `contribute_tools` call per module (batch 5c)

The tool-surface loop asks each module for its `ToolSurface` once (MCP server → identity headers, suppressed tools, declared reply tools, origin-first by `claims_source`); the three separate calls and their fail-open arms moved into `XYZBaseModule.contribute_tools`. `gather` / `contribute_instructions` / `contribute_turn_context` are the renamed hooks.

2026-09-07: `build_complete_system_prompt` renders the `prompt.sections` providers into a `PromptContext` and joins them with the bound `prompt.assembler` (platform/prompt_slots); the five former inline parts live in builtin.prompts. Part sizes / narrative meta / the dump keep their shape.

## 2026-09-07 — [SYSPROMPT-BREAKDOWN] parts follow the section registry

The parts= field is rendered from part_sizes in emitted order instead of six hardcoded ids: a plugin-contributed prompt section appears by its id and a renamed builtin section can no longer read =0 forever. The six builtin ids and the prefix-bucket hashes / ctx_sha256 are unchanged, so existing grep one-liners still match.

## 2026-09-07 — public prompt-building surface; Bootstrap lifecycle settled before sections render

build_user_temporal_block / build_module_instructions_prompt are public: prompt sections (a plugin) call them, so they are a contract and no longer underscore-private. _settle_bootstrap runs before the sections loop: the Bootstrap.md threshold check, its auto-delete and ctx_data.bootstrap_active belong to the platform, not to an optional, reorderable prompt section. RenderedSection carries each provider's budget_chars so the assembler's overrun report is live.

## 2026-09-07（round-2 P2-I4）— required prompt sections fail the prompt instead of vanishing

The section render loop caught every exception and continued with `text = None`, and an empty render
simply produced no section. Correct for a DEGRADABLE contribution ("one section must not take the
turn down"), wrong for a load-bearing one: the cloud security preamble (the 2026-06-17 incident's
mitigation) was droppable by a binding, by a `builtin_overrides` disable of `builtin.prompts`, or by
one raising provider — with a single warning and no difference visible in `[SYSPROMPT-BREAKDOWN]`.

Now: `section_required(provider, deployment_mode)` (reads `PromptSectionProvider.required_in`
defensively, so a section written against the older Protocol is treated as degradable). A required
section that renders empty or raises → `RequiredSectionMissing`, no prompt. Per MODE, not
unconditional: `SecuritySection` legitimately returns `None` on desktop, so a plain `required: bool`
would have broken every local turn. Optional sections keep their per-section isolation unchanged.
`_report_missing_section` emits the one `[SYSPROMPT-BREAKDOWN]` line before the raise (the normal
emission point is never reached), and `MISSING_SECTION_SIZE` prints as `<id>=MISSING` there, so the
existing greps see which contribution went missing rather than a subtly shorter prompt.

Also: `_expand_native_turns` asks `framework_supports_native_replay(identity.framework)` instead of
membership of `NATIVE_REPLAY_FRAMEWORKS` — the capability, not a builtin name table.
