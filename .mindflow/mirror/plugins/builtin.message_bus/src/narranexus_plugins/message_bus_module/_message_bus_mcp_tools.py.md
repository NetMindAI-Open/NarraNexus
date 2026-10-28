---
code_file: plugins/builtin.message_bus/src/narranexus_plugins/message_bus_module/_message_bus_mcp_tools.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（PR #389 M1/M7）— 回执记账失败带 `note`；owner 停止不产生回执

`_book_receipt` 的 except 分支现在给回执加 `note: "receipt bookkeeping unavailable; the message
was sent"`——docstring 早就承诺了「with a note」而代码没做。docstring 同时写明：`CancelledByUser`
路径只 ack 不盖回执，发件方回执停在 `accepted`，那是 owner 的主动决定不是投递结果。

## 2026-09-10（PR #389 I1/M2）— 两个发送动词各自的超长出路；`error` 与 `receipt.reason` 同文

`message_agent` 捕 `BusMessageTooLarge` → `oversize_reason(size, OVERSIZE_REMEDY_PARTS)`；
`message_team` → `OVERSIZE_REMEDY_TEAM`（它没有 part_*）。`message_agent` 失败分支的 `error`
不再是原文 `str(e)`，与 `receipt.reason` 同一份脱敏文本（相邻字段不能绕过脱敏，M2）。

## 2026-09-10（review r3 M3）— `message_agent` 向模型公布三条上限

docstring 点名 `MAX_BUS_MESSAGE_BYTES` / `MAX_MESSAGE_PARTS` / `MAX_MULTIPART_TOTAL_BYTES`，
数字经 `@mcp.tool(description=…)` 从 [[multipart]] 的常量格式化进注册文本——不再写第二份数字。
模型在生成 210 KB 之前就知道发不出去，而不是靠被拒才知道。

## 2026-09-10（review r2 C1）— `_book_receipt(bus, …)` 真的经 `bus.get_message` 取 channel

round-1 M1 只加了协议方法、mirror 先写了「已接线」而工具没改（谎报，r2 C1）。现在
`_book_receipt` 多收 `bus`，`sent = await bus.get_message(msg_id)`；读回失败（cloud 桩
`NotImplementedError`）只让 `channel_id=""`，回执照常落库、仍 `accepted`——never-invert 契约
不破。锁：`test_receipt_channel_comes_from_bus_get_message_and_survives_a_bus_that_cannot_answer`。

## 2026-09-09（review I4）— 超长检查移到写入边

`_reject_oversize_text` / 本地 `MAX_BUS_MESSAGE_BYTES` 删除；[[local_bus]] `send_message` 对
所有入口（含 `message_team`）统一按字节拒绝并给出分片指引，工具的 except 把 `ValueError` 文案
原样交给模型。

## 2026-09-09（review C1/M3）— 预检改用只读 `peek_skip`

`_book_receipt` 不再调 `should_skip`（turn 闸门，即将带 CAS 领取探针的副作用），改
[[circuit_breaker]] 的只读 `peek_skip`；未知/未来状态（`probing`）映射成 held。外层那圈
冗余 try 随之删掉（`peek_skip` 自身 fail-open）。

## 2026-09-09 — `message_agent` 的 `part_index/part_count` 与按字节拒绝超长

docstring 教模型：一次放不下就按序分块发（同 count、从 1 开始），收件方在最后一块到达
前不会被唤醒、收到的是原样拼回的一条——所以随便切、别摘要、别重复。返回里带
`part: "i/n"`，未到最后一块时附 `note` 说明收件方还没被唤醒。写入边的顺序校验在
[[local_bus]]，重组在 [[multipart]]。

`_reject_oversize_text`：单条/单块超过 `MAX_BUS_MESSAGE_BYTES`（60_000，留在 MySQL TEXT 的
65,535 之下）直接拒绝并点名分片——**拒绝而不是截断**（铁律 #16）：列要么在严格模式下抛
一个模型看不懂的 1406，要么静默留前缀，两者都丢尾巴。

## 2026-09-09（review C1/M3）— 预检改用只读 `peek_skip`

`_book_receipt` 不再调 `should_skip`（turn 闸门，G4 起带 CAS 领取探针的副作用），改
[[circuit_breaker]] 的 `peek_skip(to, db=…)`：任何非 ACTIVE 状态（含未来的 `probing`）→ `held`。
外层那圈冗余 try 去掉——`peek_skip` 自身 fail-open。

## 2026-09-09 — `message_agent` 返回投递回执 `receipt`

成功路径多返回 `receipt: {status: accepted|held, reason?}`（`_book_receipt`），失败路径带
`receipt: {status: failed, reason(已脱敏)}`。**`held`** 来自 pre-flight：用
[[circuit_breaker]] 的 `should_skip(to, db=…)` 问「收件方现在跑不跑 turn」——PAUSED/COOLING
的收件方会让消息在队列里躺到熔断清除（trigger 的 skip-gate），此时报 `accepted` 就是
上游 #106 那句 "build is now in progress" 的来源。工具 docstring 明说：held 的消息不要
拿去向任何人承诺开工。

纪律与 `_describe_agent` / `_record_peer_dm_inbox` 相同：**跑在发送成功之后、绝不反转
结果**——pre-flight 读失败退化成 accepted 并 warning，回执写失败只 warning。回执行写进
`bus_delivery_receipts`（[[bus_delivery_receipt_repository]]），channel_id 从刚插入的
`bus_messages` 行取（bus 找到/新开的那个 DM），不在 `MessageBusService` 协议上加新方法。
`message_team` 不出回执：房间本身就是发件方在看的面，`capped` 已经说了谁没被触达。
后续状态（processed/relayed/silent/failed/dropped）由收件方 trigger 写，见
[[message_bus_trigger]]。锁：`tests/message_bus/test_delivery_receipts.py`。

## 2026-09-09 — `message_agent` / `team_share_file` 的 docstring 对齐共享区实现（B-22 复审 M4）

B-22 让 `att_` ref 在发送方自己的 store miss 时回退到 owner 的共享 bus 区，于是"只能分享你
实际拥有的文件"这句不再成立：同 owner 任意房间（含团队群里人类上传）的附件都能按 id 解析。
两处 docstring 改成如实描述（per owner、不 per team；永远不是别的 owner 的文件）。边界分析
在 [[../../../../../src/narranexus/platform/message_bus/_bus_attachment_impl.py]]。

## 2026-09-07 — 私有平台模块换成公开门面（批 6c，A2-1）

本文件曾 import `narranexus.platform` 的下划线私有模块。批 6c 在拥有它的包上开出了
具名公开函数（`module_system` 的 caller-identity 解析器 / `marketplace` 的
store·pipeline·secret-box / `agent_framework.llm.prompt_probe_emit` /
`agent_framework.adapters.build_tool_policy_guard`），本文件改用它们。
理由不是命名规范：这些 MCP 工具和 helper 自批 6b 起是**独立的 wheel**，
「包内私有」对它们已经不成立了。`pyproject.toml` 的
`plugins never import a private platform module` 契约（只查直接 import）守住这条线。

## 2026-08-20 — `message_agent` 发送即写 Agent 收件箱

`message_agent` 成功发送后调 `_record_peer_dm_inbox`，把这条 DM 写进收发双方的
[[inbox_recorder.py]] `record_peer_message`（发送方 OUTBOUND + 收件方 INBOUND）。**为什么在
这里而不是投递侧**：peer DM 里 agent 的 `turn.text` 是对自己 owner 的独白、不是发给 peer 的
话，只有发送工具这一处同时握有「发给谁 + 发了什么」。放这里还顺带避开了投递侧的两个坑——
批次里混入的平台通告行（会被当成「peer 说的话」）、以及 turn 抛异常/poison 时 inbound 永久
丢失——因为工具只承载 agent 自己写的正文、且早于收件方任何一轮。

**永不反转已成功的发送**（同 `_describe_agent` 的契约）：记录失败只 `logger.warning` +
落一条 `inbox_write_failed` 审计行（CLAUDE.md 教训 #5：DB 痕迹比会 rotate 的 log 可靠），
绝不把已投递的消息报成 `success:false`。审计走 [[service_audit.py]] 的**公开** `event()`
(不碰私有 `_emit`),且外套一层**只包审计写入本身**的窄 try——连审计自己抛也吞掉,决不
逃逸。owner 解析不到、或收件人 id 不是真 agent(`to_row is None`,跨用户闸只挡已知的
他人 agent、挡不住编造的 id)都直接跳过,不建空 owner / 幽灵线程。守卫见
`tests/message_bus/test_agent_dm_inbox.py`(填双收件箱 / 失败仍 success+审计行 / 审计自身
抛仍 success / 空 owner 不建线程 / 收件人不存在不建线程)。

## 2026-08-19 — create_team 做实（不再是 bus_create_channel 改名）

`create_team` 现在建**真 team**：经 `TeamRepository.create_team(owner=agents.created_by)` 写 `teams` 行 + `TeamMemberRepository.add_member` 写 `team_members`，再调 `team_rooms.get_or_create_team_room`（写非-agent `team_<id>` marker、同步成员），**返回 `team_id`**（不再是 `channel_id`）。跨用户成员在任何写入前先被拒（镜像 `LocalMessageBus.create_channel` 的同用户边界），避免留下孤儿 team 行。此前只调 `bus.create_channel`：不写 teams/team_members、`created_by`=创建者、返回值 agent 无动词可用（`message_team` 查 `teams` 得 None）。端到端由 `tests/message_bus/test_create_team_tool.py` 钉住（create_team → message_team 必成功）。

## 2026-08-17 — 工具按 agent 的社交处境重命名，新增 `message_team`

`bus_*` 这个前缀命名的是一个 agent 不该知道存在的子系统。改名后名字说的是**处境**：
`message_agent` / `message_team` / `create_team` / `find_agent` / `read_history` /
`team_share_file` / `team_list_files` / `team_pin_rule` / `team_unpin_rule` /
`team_work_*`。

**删除**：`bus_get_unread`（未读已注入，且说明书写着"别调"——而 prod 上 165 次调用证明
散文劝阻不起作用，所以解法是拿走工具而不是继续劝）、`bus_get_channel_members`（team 花名册
已注入 prompt，DM 只有两人）、`bus_get_agent_profile`（Known Agents 已注入）、
`bus_leave_channel` / `bus_kick_member`（成员关系归用户管；后者在 team 房间里
**结构上永远失败**——creator-only，而 creator 是合成标记）。

**`message_agent` 合并了旧的两个 peer 发送工具（`bus_send_to_agent` / `bus_reply_to_channel`）。** 回话与主动找人是同一个动作，所以是同一个
工具；`to` 必填，因为一轮里可能有多个 peer，平台不猜（决策 ⑥）。

**`message_team` 是补上的动词**，落点 [[team_posting]]。`team_id` 必填：agent 可同时在多个
team。三道门与其它 team 工具同序：agent 存在、team 属于其 owner、agent 是成员。

**`_describe_agent` 永不抛。** 它跑在发送成功之后、在工具的 `try` 里；抛了会把**已投递**的
消息报成 `{"success": false}`，agent 会重发。一个装饰性的回显不该能反转它所描述的动作的结果。
（它此前根本不存在——`message_agent` 一被调用就 `NameError`，被 except 吞成失败。pyright 抓到。）

**`create_team` 的 docstring 原来写着「Create a new MessageBus channel」并推荐
`message_agent`。** 按词表，`MessageBus` 不得出现在任何 agent 可见文本里。
## 2026-08-14 — bus_list_team_files 补上漏掉的 get_db_client 导入

该工具自 2026-08-07 落地起就引用了未导入的 `get_db_client`（本文件的 db 导入全是**函数内局部**——82/396/463/498 各在别的函数作用域，闭包解析不到），每次调用必炸 `NameError`，而 [[test_list_team_files_tool]] 原有测试只测 impl 不过 wrapper，全绿假象。修复=补函数内导入（与兄弟工具同款，保持模块加载期不引 db_factory 的循环导入规避）+ 按兄弟工具惯例整体包 try/except（review Minor-4：此前它是这批 bus_* 里唯一裸抛的——连接池懒构建失败会把原始异常甩给模型；except 只回 `{"success": False, "error": ...}`，**不补 `files: []`**，拒绝≠空文件夹）。新增走 `register_message_bus_mcp_tools` 注册面的 wrapper 回归测试。教训：MCP 工具的测试必须打到注册的 wrapper，不能只打 impl。

## 2026-08-07 — 两个发送工具盖上 root_run_id

`bus_send_message` / `bus_send_to_agent` 把 `caller_root_run_id()` 写进
`bus_messages.root_run_id`。这是血缘链**唯一的断点**:工具跑在共享的 MCP
进程里,除了注入的身份之外对调用方一无所知,而它写出的这条消息正是下一个
run 的触发源。与 `_send_turn_source` 同理——只有 send 现场知道自己的目标。

## 2026-08-07 — 新增 bus_list_team_files

共享目录终于可被枚举。工具本身是薄封装，规则在 [[team_files.py]]：授权按 **成员关系**
而非 owner（一个 user 多个 team，按 owner 判会让该 owner 的任意 agent 读到全部 team）。

配套：team prompt（[[message_bus_trigger.py]]）从「用 Read 打开这个目录」改为**明确指向本工具**
——此前 agent 只能猜路径或让别人复述，发现文件靠模型之间的社交协议。

## 2026-08-05 — 删掉 `bus_register_agent`：名录不能有第二个写入者

`bus_agent_registry` 现在是 A2A 发现的权威行，而这个工具是它的**第二个写入
者**，且写 `owner_user_id=""`（源码原注释是 "Will be filled in by the caller
context"，但没有人填）。`LocalMessageBus.search_agents` 的 where 带
`AND owner_user_id = ?` —— agent 只要调一次，它立刻从**同 owner 的搜索结果里
消失**，直到下一轮 hook 把行修回来；顺带还重置 `registered_at`。它声明的
capabilities 也会被每轮的机械推导无声覆盖，即工具承诺的事它做不到。

按铁律 #2 直接删（不留薄壳）：描述归 [[awareness_module]] 的
`update_agent_profile`，capabilities 从技能+模块推导。
[[message_bus_module]] 指令里那句「除非要更新 profile 否则不要调
bus_register_agent」同批改写成指向 `update_agent_profile` —— 原文恰好在
「想修 profile 的时候」把模型送去调这个会破坏行的工具。有两条测试分别钉住
源码里不再出现该工具、以及指令不再提它。

## 2026-08-03 — `_send_turn_source`:章按「这一条发给谁」定,不按整轮定

两个发送工具不再直接写 `caller_turn_source()`,而是走
`_send_turn_source(to_agent=… / channel_id=…)`:先取轮次种类,只有当**本条
send 的目标**等于本轮差事作用域(`caller_errand_scope()`,见
[[_mcp_identity]])时才升级成 [[hook_schema]] 的 `BUS_ERRAND_TURN_SOURCE`。

**为什么不能整轮盖章**(同 PR 内自我推翻的做法):
`MessageBusModule.gather` 每轮把**跨所有 channel** 的未读
(`bus.get_unread`)注进 context,模块提示词又**要求**回答它们(「A question
is never ping-pong」)。所以差事延续轮次里顺手回答别的同伴 C 是平台自己引导
的常规路径;整轮盖章会把那条**回答**标成提问,C 于是不再向自己 owner 回报
—— P1 换个座位复发(2026-08-03 review round 4)。

已记录的残余:发进「恰好是差事 channel 的群 channel」会把每个成员那份都盖成
提问(bus 差事跑在自动建的 DM 上,要手工建群当差事 channel 才踩到)。

## 2026-08-04 — 两个发送工具都记录本轮种类

`bus_send_to_agent` 与 `bus_send_message` 都调 `caller_turn_source()` 并传给
bus,让消息自己带上"这是提问还是回复"。**两个都要**:它们写同一张表、
同一个消费方(`_incoming_is_reply_to_my_errand`),漏一个就让那条路径落降级。
turn source 同时走显式 header 与 bearer,所以 codex 上也读得到
(见 [[_mcp_identity]]);读不到时传 None,触发侧按未知降级。

## 2026-07-20 — file attachments + team share

`bus_send_message` / `bus_send_to_agent` gained `attachment_refs` (comma-separated
`att_` file_ids and/or workspace-relative paths); `_stage_send_attachments` resolves
the sender's owner (`agents.created_by`, dialect-safe via `get_db_client`) and stages
the files through [[_bus_attachment_impl]] before send. New `bus_share_to_team`
validates ownership + membership (`teams` / `team_members`) then publishes a file into
the team's shared scratch dir (a server-side write — agents can't write `_shared`
themselves under the cloud sandbox). owner_user_id is always looked up, never taken
from the LLM.

# _message_bus_mcp_tools.py — MessageBus MCP 工具函数集合

## 为什么存在

`MessageBusModule` 通过 MCP 服务器向 LLM 暴露工具，但工具函数的具体实现不应该直接写在 Module 类里（会让 `message_bus_module.py` 变成一个巨型文件，且工具函数需要独立可测试）。`_message_bus_mcp_tools.py` 把所有 MCP 工具的实现提取出来，成为可以独立注册到 MCP 服务器的函数集合。

命名前缀 `_` 表示这是 Module 的私有实现，不被包外直接引用。

## 上下游关系

**被谁用**：`MessageBusModule.mcp_server()` 返回的 `MCPServerConfig` 里包含工具列表，MCP 服务器框架（`module_runner.py`）把这些工具函数注册到 MCP 协议上暴露给 LLM。

**调用谁**：每个工具函数接受一个 `port` 参数（MCP 服务器端口）和一个 `get_db_client_fn` 参数（工厂函数，调用时返回 DB 客户端）。工具函数内部用这个工厂函数创建 `LocalMessageBus` 实例，调用 `MessageBusService` 的对应方法。这种依赖注入方式避免了工具函数持有全局 DB 状态。

## 设计决策

工具函数签名遵循系统约定的提取模式（`standalone function taking (port, get_db_client_fn)`）——这是为了避免与 `MessageBusModule` 类的循环导入问题，也让工具函数可以在没有 Module 实例的环境里（比如测试）独立运行。

工具覆盖了 MessageBus 的完整操作面：发消息（`send_message`、`send_to_agent`）、查询（`get_unread`、`get_messages`）、频道管理（`create_channel`、`join_channel`、`leave_channel`）、Agent 发现（`search_agents`、`get_agent_profile`）。**注册不在这一面上**——名录行由 [[agent_discovery_sync]] 单点重算，见上方 2026-08-05 条。

## Gotcha / 边界情况

工具函数里的错误处理：一般返回 `{"success": True/False, "error": "..." }` 格式，不会向 LLM 抛出 Python 异常。LLM 需要检查返回值里的 `success` 字段来判断操作是否成功。

每个工具调用都会新建 `LocalMessageBus` 实例（通过 `get_db_client_fn()`），而不是复用同一个实例。这不是性能问题——`LocalMessageBus` 的 `__init__` 只接受一个已有的 backend 引用，构造成本极低，且避免了状态共享问题。

## 新人易踩的坑

工具函数名（如 `"send_message"`）就是 LLM 调用时使用的工具名，必须和 MCP 服务器注册时的名称一致。如果修改函数名，需要同时更新 `MessageBusModule.mcp_server()` 里注册工具时使用的名称字符串，否则 LLM 调用会报"工具不存在"。

## 2026-08-11 — `bus_pin_team_rule` / `bus_unpin_team_rule`

薄包装，规则在 [[team_bulletin]]。**没有 `team_id` 参数**：来自
`caller_team_id_from_request()` 的服务端身份头。这比隔壁 `bus_share_to_team`
（模型传 team_id + 三段校验）更强——agent 无法指认自己当前不在的团队，
于是跨团队写入不是要防的攻击，而是**不可表达的状态**。有测试断言签名里没有 `team_id`。

## 2026-08-14 — `bus_send_message` 补盖 `event_id`:归因缺的那一半

此前只盖 `root_run_id`(触发树的根,用来续 cascade),没有盖**这一轮**的 id。后果不在
这个文件里显形,而在 trigger:团队房要判断"平台没代发的这一轮,房间到底听没听见这个
agent 说话",平台自己代发的那条消息盖了 turn id、agent 用本工具发的那条没盖,于是同一个
问题只有一半能被回答 —— 剩下那一半只能靠猜,而猜错就是在一个**已经听见回复**的房间里
再贴一条"投递失败"。

`event_id` 从 `_mcp_identity` 的请求头取(`caller_event_id_from_request`),不是模型
参数;artifact 工具早就是这么记归因的,这里只是把同一条路补齐。

## 2026-08-14 (补) — `bus_send_to_agent` 一并盖章,并补上真入口测试

只给 `bus_send_message` 盖 `event_id` 会让 `bus_messages.event_id` 的含义取决于
写它的是哪个工具。两处一起盖。

这半条链此前**零测试**:trigger 侧的用例都是桩里自己写一行带 `event_id` 的消息,
等于把「工具会盖章」当前提写死,而不是验证它。而
`caller_event_id_from_request()` 设计上就是**头缺失即静默返回 None** —— 头注入链
(context_runtime 传参 → `agent_id_headers` → adapter 转发 / bearer 第 8 段)任何一环
断掉都不会有测试变红,症状却是团队房里偶发的假 ⚠️。
`test_bus_send_event_id_stamp.py` 走注册后的真工具函数 + 伪造 ambient request 头,
并把「无头 → None」这条降级契约也钉住(实测过去掉盖章四条全红)。

## 2026-08-18 — `read_history` 改按会话把手；错误文案不再点名子系统

签名从 `(agent_id, channel_id, limit)` 改为 `(agent_id, with_agent, team_id, limit)`：
agent 的世界里是私聊和团队，一个收 channel_id 的工具是它唯一必须知道别的东西的地方 ——
而为了能调用它，那个 id 就得被印进上下文，词汇于是又回来了（见 [[message_bus_module.py]]
同日条目）。恰好给一个，两个都不给或都给都是明确报错。

新增 `_resolve_conversation`：**成员资格由查询本身保证**，不是查完再检查 —— 私聊那条
join 以调用者自己的 id 为连接条件之一，团队那条要求 `team_members` 行。先找频道后授权的
形状，离「漏一个分支就能读到别人的会话」只有一步，而那种分支往往是修别的东西时顺手加的。
`%s` 而非 `db.placeholder`（调用者持 AsyncDatabaseClient，见 [[team_posting.py]] 的教训）。

`"MessageBus not available"`（5 处，返回给 agent）改成不点名子系统的文案：让模型对一个它
没有心智模型的组件做推理没有意义，而唯一有用的下一步（本轮别再试着发）两种写法都一样。
`find_agent` 的 docstring（模型会读的工具描述）同改。

MySQL twin：`tests/message_bus/test_team_posting_mysql.py` 覆盖这条三表 join 与团队分支。

## 2026-08-18 (二) — 两个发送动词都补上内容校验

路由参数（`to` / `team_id`）从一开始就校验，`text` 从来没有 —— 而后者是更要紧的一半。空白
文本会把空气泡张贴进一个人在读的界面，随后 `has_message_from_turn` 对这一轮答 True，于是
「什么都没说」的通知被抑制、整轮被记为**已投递**。一个看起来被回答了、实际什么都没说的房间，
比它取代的沉默更糟 —— 沉默至少还会产出一条通知。私聊那边更糟：空消息会给收件人起一整轮 LLM。

返回**错误**而不是静默 no-op：一个对 no-op 返回 success 的工具，会教模型它已经回复过了。
同一条纪律 `inbox_recorder.record_turn` 早就对空 outbound 行用了，只是没被用到「现在每个团队
轮次的回复」这个工具上。[[team_posting.py]] 侧另有一道 raise 作为内层保险，专门拦绕过工具的
调用方（测试 helper `speak_in_room` 就是一个）。

## 2026-08-18 (三) — `read_history` 三处：原语、上限、自我私聊

**原语选错了。** 它调 `bus.get_messages`，那是 `ORDER BY created_at ASC LIMIT n` ——
房间**最旧**的 n 条。于是在任何超过 limit 条的会话里，agent 问「我看到的这些之前发生了什么」，
拿到的是开场消息，中间有一个无界的静默空洞，而它读起来像当前上下文。`get_recent_messages`
的 docstring 自己就写着 `get_messages`「对 recent scrollback 是错的」—— 而那正是这个工具
docstring 的承诺。改用最近 n 条：agent 拿到的窗口是尾部，最近 n 条严格包含它并向前延伸，
没有空洞。（`get_messages_before` 是更精确的原语，但它要一个时间戳游标，而「时间戳」正是
agent 世界里没有的词汇。）

**`limit` 没有上限。** 调用方可控且无界，`limit=100000` 会把 10 万行塞进工具结果、撑爆上下文
窗口、让这一轮在半途死掉。本模块其他每一个 agent 可见的读都有上限
（MAX_UNREAD_IN_CONTEXT / MAX_KNOWN_AGENTS_IN_CONTEXT / TEAM_HISTORY_LIMIT）——
这是唯一一个把上限交给模型的。新增 `READ_HISTORY_MAX`。

**`with_agent == 自己` 匹配任意私聊。** 两个 join 都被同一个 id 满足，于是返回一条**任意**的
无关会话 —— 静默地，且读起来像一份可信的记录。现在明确拒绝。

**私聊查询与 [[local_bus.py]] 的那份重复。** 逐字节相同的三表 join，只差 `%s` 与 `{ph}`，
而「什么算一条私聊」正是那种会只在一处被改的事实（archived 标记、去重、跨 owner 规则）。
现在共享的是 **SQL 文本**（`direct_channel_sql(ph)`），各调用方带自己的占位符 —— 共享
execute() 会强迫其中一方用错占位符，而那正是 `_room_labels` 在 SQLite 上静默返回空的成因。
同时加了 `ORDER BY created_at ASC`：`send_to_agent` 在查不到时会建频道，两个并发首发可以
都查不到、都建，此后无序的 `rows[0]` 依赖引擎，发送方与历史读取方会对「这段会话是哪个频道」
产生分歧。
