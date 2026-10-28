---
code_file: plugins/builtin.teams/src/narranexus_plugins/teams/routes.py
last_verified: 2026-09-10
stub: false
---

## 2026-09-10（PR #389 I1）— 400 文案面向人

`oversize_reason(bytes, OVERSIZE_REMEDY_HUMAN)`：事实 + 「消息过长，请拆分后发送」，不再提
part_index/part_count。

## 2026-09-10（review r2 I2）— `send_team_chat` 超长返回 400

bus 写入边（[[local_bus]]）对超过 `MAX_BUS_MESSAGE_BYTES` 的行抛 `ValueError`，这是用户手打
文本上 bus 的唯一 HTTP 入口，不拦就是 500。现在按 **strip 之后**的 UTF-8 字节数在入库前判，
`HTTPException(400, multipart.oversize_reason(...))`——文案与 bus 同源，不另写常量。
锁：`tests/backend/test_team_chat_oversize.py`（超一字节 400、刚好上限 200）。

## 2026-09-09 — 公告栏 GET/POST 曾把整个 dict 喂给 `format_for_api`（B-18；复审 I4/M6 修订）

`format_for_api` 只格式化**单个** datetime；喂给它一整个 `entry.model_dump()`
会在函数内部撞上 `dt.tzinfo` 的 `AttributeError`，落进 except 分支返回
`str(dt)`——也就是整个 dict 的 Python repr 字符串。前端拿到的 `entries` 因此
是一串字符串而不是对象。**真实症状**（复审核对过 `TeamBulletinPanel.tsx:69`）：
过滤器是 `e.source !== 'auto_summary'`，字符串上 `e.source` 是 `undefined`，
`undefined !== 'auto_summary'` 为 true——条目**一条不丢**，全部进 `renderEntry`，
渲染出 N 行 `content`/`entry_id` 都是 undefined 的空白行、React key 全 undefined。
不是"面板永远为空"（第一版 mirror / docstring / 测试注释都写错了，同批改正）。
`_bulletin_entry_for_api` 按类型转换但**只转顶层**（复审 PR#393 M8，docstring 已写明）：`BulletinEntry`
今天是扁平的；将来加嵌套子模型或 `list[datetime]` 字段时要在 helper 里显式转换，否则 FastAPI
会把它们序列化成不带 `Z` 的 ISO。

修法是 `_bulletin_entry_for_api(entry: BulletinEntry) -> dict[str, Any]`：`model_dump()`
后遍历所有值，凡 `isinstance(value, datetime)` 的都过 `format_for_api`，**按类型不按字段名**
——将来 schema 加第三个 datetime 字段不会绕过转换、被 FastAPI 序列化成不带 `Z` 的 ISO
（`timezone.py` mirror 警告过的 `new Date()` 本地时区陷阱）。`list_team_bulletin` 和
`create_team_bulletin_entry` 都改用这个辅助函数。`format_for_api` 自身现在对非
datetime/str 抛 `TypeError`（同批 [[../../../../../../src/narranexus/platform/utils/timezone.py]]），
根因收口。本文件里其余 `format_for_api(...)` 调用点（**文件级** 14 处；仓级 38 处由复审
独立核过）全部传单个字段，没有同类漏洞。

## 2026-09-07 — 宿主依赖改走 `narranexus.sdk.web`（批 6c，G2-I1）

批 6b 把 router 搬进插件包时，`from backend.*` 没跟着走：本文件当时还在 import 宿主的
私有模块（`routes._ownership` / `routes._mcp_egress` / `routes.dashboard.routes` 的下划线
函数）或非契约的公开符号（`backend.auth` / `backend.auth_errors` / `backend.config`）。
现在全部改成 `narranexus.sdk.web` 的 seam（实现见 [[plugin_sdk_host]]，形状见
[[web]]）。这不是改 import 路径的洁癖——第三方照抄这个 router 时，
`assert_owned` / `resolve_current_user_id` 一个都拿不到，只剩「自己重写鉴权」
（2026-08-12 那批 IDOR 的来源）这一条路。`pyproject.toml` 的
`plugins never import the host (backend)` 契约把这条线钉死，豁免列表为空。

## 2026-09-03 — idle 条目带 `last_turn_silent`

`_member_activity` 的 idle 分支多一个布尔 `last_turn_silent`(来自
[[../../src/narranexus/platform/message_bus/_bus_activity]] 的 `last_turn_was_silent`)。
**queued 态也带**(与 idle 同样要求 `steps["items"]` 非空;row 里仍是上一轮的 steps;running/stalled 不带——`start()` 已重写 steps,读出来是假的「说过话」)。
房间不再为沉默贴行,「跑了但没说」和「没跑」靠花名册这一格区分,owner 追问把成员排进队列时标记不丢。
同日 `get_team_chat` 响应加 `patrol_enabled`(`_patrol_enabled(team)`):巡查开关随房间轮询下发,
前端 teamsStore 单一副本,管理 tab 不再自己拉整张工作板。
测试:`test_team_activity_payload.py::test_idle_reports_whether_the_last_turn_was_silent`。


## 2026-08-17（二）— 只带附件的交接也有个说得出口的标题

这条路由是**唯一**允许正文为空的开项入口（agent 回帖必有文本），所以
[[errand]] 里 `_title_from` 的 `(untitled hand-off)` 兜底只会在这儿冒出来。板子
被注入每个成员每一轮的 prompt，一行读不出内容的项就是纯粹的 token 浪费。

传参而不是改 `_title_from`：兜底文案是共享出口，改它会同时影响 trigger 路径；而
且只有路由知道被交接的是附件。

## 2026-08-17 — 用户发的 @ 也进工作板

`POST /{team_id}/chat/messages` 在 `send_message` 之后调 [[errand]] 的
`record_handoffs`。

在此之前 [[errand]] 只挂在 [[message_bus_trigger]] 上，也就是**agent 回帖**走的
路；人发的消息从这个路由直接进 bus，于是「@Bruno 把数拉一下」被无视之后什么痕
迹都没有。而这恰恰是**用户唯一亲眼看得见**的那类断链——agent 互相无视用户看不
见，自己被无视看得见。同时也修了闭环率报告的分母：只量 agent→agent 那一半，回答
的是另一个问题。

三条边界：无 @ 时路由到默认响应者，那是**平台挑人回答**不是用户派活，不开项；
`@all` 映射成 `@everyone` 后被 helper 自己滤掉；记账失败只记 warning——消息已经
在房间里了，让用户重打一遍所有人都已看见的东西是最差的结果。

不调 `close_delivered_errands`：用户不是 assignee，没有什么可结的。

## 2026-08-15 — 时间比较走 `event_time_str`；跨层不变式改成真问两边

跨房间比较原本手写 `str()`，那是这个仓库里**第四份**手抄——`utils/db/dialect_time.py` 的
`event_time_str` 就是为这件事存在的（sqlite 驱动给 `datetime`，mysql 给字符串），而且它自己
的来历就是"被拷进两个审计仓库、又被一个 backend 路由跨包 import 私有名"之后收口的。两者
也不等价：`str(None)` 是 `"None"`，字典序上高于任何真实时间戳，会让一个 NULL 行永远胜出。

`test_the_mark_and_the_transcript_agree_on_precision` 之前**一次都没碰过 transcript**：
两边都是测试自己调 `format_for_api`，等于只断言了"activity 这一侧用的是它"。名字里写着
`agree`，却只问了一方——而它 docstring 里描述得最细的那个失效场景（transcript 那侧换个
formatter，圆点开始偶发消失）恰恰是它抓不到的。现在真跑一次 chat 路由，拿渲染出来的
`created_at` 和 `last_message_at` 比。

`_raw_at` 那个"塞进响应字典再摘掉"的形状也换了：原始时间戳现在活在一个函数内的并行 map 里，
从构造上就到不了调用方——不再需要一条"内部字段没泄漏"的测试来守它。

## 2026-08-14 (三) — 跨房间比较要用原始时间戳

`format_for_api` **截断到整秒**。用它的输出做"哪个房间更新"的比较，等于同一秒内说话的两个
房间会平手、然后由结果集顺序随便挑一个——这个测试单独跑过、在全量里失败，正是因为它断言的
是墙上时钟而不是规则。现在比较原始列，格式化只用于上线的那一份，并且内部字段在返回前摘掉。

同时钉住一条跨层不变式：`last_message_at` 和 transcript 的 `created_at` **来自同一个
formatter**。客户端把"已读水位线"推到它**渲染过**的最新 `created_at`，再拿它和这个
`last_message_at` 比——两边精度不一致的话，:00.800 的回复会被当成 :00.000，对上 :00.500 的
水位线，圆点就不出现，偶发、只差一条消息。一致的粗粒度才让这个比较成立。

## 2026-08-14 (二) — 一个 team 有多个房间时取最新的那个

`channel_to_team` 是多对一，循环里直接覆盖同一个 `team_id` 意味着"结果集最后一行赢"，而
不是"最新的赢"。现实中一个 team 一个房间，所以今天不会发生——但它错的方向是**水位线会
倒退**，表现为"标记怎么清都清不掉"，那会被当成计数的 bug 排查。

同一轮把 `test_the_route_opens_on_the_newest_page` 从读源码改成**真的走一遍 HTTP**
（`PAGE_SIZE` monkeypatch 成 3，塞 6 条消息，断言拿到的是最后 3 条）。源码断言对这个 bug
是反的：重命名会红，行为回归会绿。

新增 `tests/backend/test_team_room_activity_mysql.py`（`NARRANEXUS_MYSQL_TEST_URL` 门禁）：
这是本次唯一一段**方言差异会体现在结果而不是报错**上的 SQL——`created_at` 在 SQLite 是
TEXT 字典序、在 MySQL 是 `DATETIME(6)`，而查询在 `MAX()` 和自连接里各比较了一次。已对真实
MySQL 8 跑过。

## 2026-08-14 — 房间活动一次查完；`is_platform` 由服务端回答

`_team_room_activity` 从"每个房间一次查询"改成 **MAX + 自连接，一次查完**。这个端点被
每个打开的标签页每 30 秒轮一次，所以按房间查会同时乘以团队数和标签页数。用 `MAX` 子查询
而不是窗口函数：`ROW_NUMBER() OVER` 需要 MySQL 8，而这个代码库其余部分并不要求它。

`get_team_chat` 的每条消息多带一个 `is_platform`。此前前端自己维护了一份
`PLATFORM_MSG_TYPES` 的镜像 Set，注释还声称"有测试守住"——那个测试是**第三份**手写拷贝，
而且已经比另外两份少了两项。线上传的是字符串：服务端开始发一种前端不认识的类型，那条平台
通知就会被渲染成**成员发言**（带身份色、头像，名字位置是 `team_<id>` 这种 marker）。这个
tuple 光在本分支里就从 5 项长到 7 项，"记得改另一份"从来不是一种机制。

## 2026-08-14 — 房间打开的是**最新**一页（一个一直存在的严重 bug）

`get_team_chat` 之前是 `get_messages(channel_id, since=since, limit=200)`，而
`get_messages` 是 `ORDER BY created_at ASC LIMIT n` —— **最旧的 200 条**。于是一个说过
超过 200 句话的房间，永远打开在它的第一天；而之后每次轮询都用 `since` 从屏幕上最新那条
**往前**走，所以它就停在史前时代了。没有任何东西看起来是坏的，它只是永远显示了会话的错误
一端。

现在三种模式：无游标 = 最新一页（`get_recent_messages`），`since` = 往后追（3 秒轮询），
`before` = 往上翻历史（见 [[local_bus.py]]）。三个方向共用同一个 `PAGE_SIZE`，读者无法
从页面大小反推自己拿到的是哪一种。

值得记的是：`get_recent_messages` **一直都在**，路由调的是另一个。

## 2026-08-14 — `list_teams` 带上房间活动；一个被装饰器吃掉的 handler

`_team_room_activity` 给每个 team 回答一件事：**这个房间上一次说了值得回来看的话
是什么时候**。sidebar 从不加载 transcript，所以「我不在的时候有事发生吗」在客户端
根本推不出来；而未读水位线又在 localStorage 里、逐设备，服务端也无从知道。于是切成
两半：服务端给时间戳，客户端拿自己的水位线去比（见 [[unread.ts]]）。

**两条排除决定了它有没有用**：

- 用户自己的消息不算 —— 否则发一条消息就会给自己刚发消息的那个房间打标记；
- 平台自己的通知不算 —— 公告通知是在**用户自己编辑公告**时发出的，名册通知是在
  用户自己增删成员时发出的。给这些打标记，等于告诉用户「有人回你了」，而唯一动作
  的人是他自己。

排除用的是 `PLATFORM_MSG_TYPES`（[[system_messages]]），而不是手写字符串——这个
tuple 存在的全部理由就是这类过滤器各写各的会漂移。方向是**排除平台类型**而非
**放行已知类型**：以后新增一种普通消息，在排除式下正常显示，在白名单下会隐形——
一个正在说话的房间读起来像哑的。两种失败不对称。

**它不创建房间**。列 team 是读操作；给每个用户从没打开过的 team 都物化一个 channel，
等于 sidebar 看一眼就把房间建出来了。

同一次改动里修掉一个真 bug：`_announce_roster` 被插在了
`@router.post("/{team_id}/members")` 和 `add_member` 之间，装饰器抓住了紧随其后的
那个函数——于是「加成员」这个接口打到了一个私有 helper 上（它的第一个参数是数据库
客户端），而 `add_member` 无人可达。import 不报错、没有测试覆盖。
`tests/backend/test_route_registration.py` 现在按命名约定守住整类问题：`_` 开头的
函数不应该在回答 HTTP。

## 2026-08-10 — Clear team data 增加 board 作用域

`_wipe_team_data` 增加 `clear_board`,端点增加 `board` 查询参数。

**独立作用域,不并进 `clear_chat`**:两者回答不同的问题 —— 聊天是「说过什么」,
板子是「还欠什么」。清掉一段吵闹的对话记录的人,几乎不会同时是想「顺便忘掉我们
说好要做的事」的人;反过来,放弃这批工作也不该要求先擦掉历史。

`board` 默认 **False**:已有调用方请求清聊天时,并没有请求丢掉团队欠账 ——
默认打开会悄悄扩大它们的爆炸半径。

## 2026-08-10 — 工作板端点(只读 + 恢复)

`GET /work-items`、`POST /work-items/{id}/resume`、`PUT /patrol`。

**刻意没有创建/删除**:板子由 lead 通过 MCP 工具维护,一块用户还能手改的板子
会和 lead 被问责的那块漂移。用户这一侧的动作只有两个:看,以及把停止 park 掉
的项**恢复**。

与 agent 侧列表的关键差别:**这里返回 `paused`**。那是停止留下的状态,而决定
要不要恢复的是用户 —— 像 agent 侧那样隐藏它,会让被停的任务看起来像被删了。

`Team` schema 相应增加 `patrol_enabled` / `last_patrol_at` 两个**只读**字段:
`_entity_to_row` 不写它们,否则一次无关的 team 编辑会把巡查游标清掉。

## 2026-08-21 — `get_work_board` 把 auto 交接折叠成交接卡

`_assemble_work_board(visible, name_by_agent)` 取代了原来「一行 = 一个
`WorkItemView`」的直映射。动机:一条 @ 了多个 agent 的消息在 `errand.py` 会扇出
成**每人一行**的 `origin=auto` 单,每行复用**发件人首行**当 title。逐行返回就让
同一句话在板上出现一次每个收件人,且挂在**没说这话的人**名下。

新规则:

- `origin=tool` 行 → `kind="task"` 卡,一对一,`title`/`assignee_name` 不变。
- `origin=auto` 行 → 按 `source_message_id` **合并成一张** `kind="handoff"` 卡:
  `source_name`(取 `created_by` 解析出的名字)、`assignee_names`(仍欠回复的人)、
  `item_ids`(底层所有行,供 paused 时逐行恢复)。`title` 置空 —— 发件人的话不再
  上板(那是错配的根源)。状态用 `_handoff_status` 聚合:任一 `stalled` 则整卡
  stalled,否则任一 ACTIVE 则 in_progress,全 paused 才 paused。
- 顺序仍按 `visible` 的首次出现(即 `created_at, id` 升序),交接卡落在它最早那行
  的位置。

**发件人名字**:handoff 的 `source_name` 有两种来源。队友(agent)走
`name_by_agent`;**用户自己发的 @** 走 `created_by="usr_<user_id>"`(见本文件
`POST /messages` 的「用户自己的交接也上板」),`name_by_agent`(只含 agent)解析
不到 —— 必须用 `UserRepository.get_display_name(user_id)`(与消息列表 `author_name`
的 `(user_name or "You")` 同一先例)。因为端点入口 `_owned_team` 已保证看到的
`usr_*` 就是发请求的 owner 本人,不用再查第二个人。**多 @ 最常见的正是人 @ 一群
agent,漏了这条会把 `usr_<uuid>` 原样当名字摊在板上。**

**paused 子集**:handoff 的 `status` 是聚合值(stalled>active>paused),但把 N 行
压成一张卡也把 N 个 resume 按钮压成一个。若只 resume 成功一半,卡片聚合成
`in_progress`、resume 入口就消失,剩下的 paused 行既不被 patrol 捞(`list_active`
不含 paused)也不被用户看见 —— 永久搁死。故 `WorkItemView.paused_item_ids` 把仍
paused 的行**显式列出**(task 卡则是它自己或空),前端据此决定 resume 可见性并只
恢复这些行。paused 是独立维度,不能挤进单值 `status`。

**边界**:只改看板这一条读路径。patrol/stalled 走 `list_active`、errand 开/关单
走 `list_open_errands`,都不经过 `list_visible`(唯一调用方就是本端点),所以折叠
显示不影响开单/关单/巡查。改 `_assemble_work_board` 时别把 origin 过滤下沉到
`list_visible`/`_list_by_status` —— 那些方法被 patrol 共用,过滤会误伤停滞检测。

## 2026-08-10 (方案 B 的后果修正) — `clear_files` 级联删除团队 artifact

**同一条规则改了两次，第二次才是重点。**

初版 `clear_files` 连 artifact 一起删，理由是「artifact 指向被删的树」——当时**不成立**（生产者
workspace 是第一允许根，内容还在），所以我撤回了级联。

方案 B 要求团队 artifact **必须**住在团队目录之后，那个理由**变成真的了**，级联也随之正确。
变的是世界，不是推理：rmtree 现在会销毁每一个团队 artifact 的内容，留下行就等于面板列出内容已
消失的 artifact，而 `heal` 也救不回来。

> **2026-08-10 更正**：上面这条结论仍然成立，但当时给的理由错了。原文写的是「`heal` 从不传
> `team_id`、短路只看 agent workspace」——那是 `heal` 当时**自身的 bug**，已在同轮修掉（见
> [[heal.py]]）。真正的理由更简单：`heal` 只能把指针**重新接回仍然存在的文件**，而这里文件本身
> 被删了。**拿一个 bug 当另一处设计的论据，bug 修掉那天论据就跟着塌了。**

`clear_artifacts` 仍可单独使用（删 tab、留文件）。

## 2026-08-10 (review 修正) — `_team_files` 钉死 wire shape + 时间戳带时区

`SELECT *` 把 `id` / `owner_user_id` / `content_hash` 带进了 API 形状（owner-only，不构成泄露，
但形状应当是**选定的**而非从表继承）。现在显式列字段。

`created_at` 归一为 **offset-aware** ISO。原值是 UTC 但**无标记**（SQLite 的
`datetime('now')` 给 `'2026-08-07 12:34:56'`，MySQL 给 naive datetime）——按 ES 规范，不带
offset 的 date-time 被当作**本地时间**解析，于是 UTC+8 用户刚分享的文件显示成「8h ago」。
artifacts 那半边没这个问题，因为它走 `Artifact` 模型、`parse_dt` 会补 UTC。

## 2026-08-08 (review 修正) — 三个独立 scope，且 delete_team 带走工作台

**撤回一处过度删除**：初版让 `clear_files` 连团队 artifact 一起删，理由写的是「团队 artifact
指向的正是被删的那棵树」——**这在最常见情况下不成立**。`_resolve_entry` 把生产者自己的
workspace 保留为**第一个**允许根，团队目录只是追加的第二个，所以按常规方式注册的团队 artifact
指向的是生产者 workspace，内容根本没被删。删它们的行等于销毁指向仍存在文件的指针。

现在 `clear_files` 只删共享目录 + `team_files` 索引。确实住在共享目录里的那些会变成**破损指针**
——这是 [[artifact_service.py]] 的 `heal` 已能恢复的状态，远好过把本来没事的那些一起丢掉。

**`delete_team` 现在先全清再删 team**。团队一旦消失，它的 artifact 对**所有查询路径都不可达**：
私聊面用 `team_id IS NULL` 排除、`list_by_team` 需要已不存在的 team、并集查询 join 的
`team_members` 下一行就被清空。**读不到的行才是验收 #7 说的孤儿**，不是「无害垃圾」。

新增 `clear_artifacts` scope 承载这件事；两个既有开关语义不变，前端对话框无需改动。

## 2026-08-07 — chat messages 透出 msg_type

`"system_stop"` 标记 owner 停止留痕,前端据此渲染成系统行而不是"这个 agent
在说话"(文案走 i18n,DB 不知道读者的语言;`content` 存英文兜底给 memory
索引这类只读文本的消费者)。普通消息仍是 `text` / `multimodal`。

## 2026-08-07 (三次) — `GET /{team_id}/artifact-turns`

消息下方芯片的数据源：`event_id → [artifact_id]`。join 键是 events 行 id，transcript 和
`instance_artifact_history` 两边都带它。

**为什么不用时间戳近邻**：一轮可以注册两个 artifact，两个 agent 也可以在同一房间同时回复——
按时间就近匹配会把产出挂到错误的消息上。

**更新型 turn 也纳入**：重新注册正是队友接力的方式，那一轮恰恰最值得浮现。`event_id` 为 NULL
的行（历史数据、或调用方无 event 在作用域）直接跳过，不归到占位分组里。

## 2026-08-07 (二次) — 团队 artifact 的 view-token

`POST /{team_id}/artifacts/{artifact_id}/view-token`。

**token 载荷刻意不动**。既有设计里 token 就是「针对某一个 artifact 的 bearer 能力」，授权
发生在 **mint 那一刻**——所以团队校验放在这条路由里，签发时仍用**产出者**的 agent_id，
而那正是 raw serving（[[raw_access.py]]）解析所依据的字段。下游一行都不用知道 team 的存在。

为什么必须换一套校验：agent 侧的 `_get_owned_artifact` 要求调用方 agent **就是** artifact 的
agent——这在团队里恰好是反的，面板本来就展示多个成员的产出，队友打开同事的 artifact 是常态
而非攻击。`_authorize_team_artifact` 改判 **artifact 属于这个 team**。

拒绝一律 404（与 agent 路由一致）：换成 403 会让探测者据此枚举出哪些 artifact_id 存在。
`team_id IS NULL` 的私有 artifact 同样通不过这条比较——**拥有一个 team 不是通往该 owner
私有产出的入口**。

可行的前提（已核）：`resolve_raw_file` 的路径约束是 `base_working_path` 而非 agent workspace
（`raw_access.py:97`），所以落在团队共享目录里的 artifact 本来就能正常提供。

## 2026-08-07 — 工作台读取路由 + 清理链路跟上新表

新增 `GET /{team_id}/artifacts`（团队面板，**不按 agent 过滤**——面板是团队的，谁产出的都算，
`agent_id` 留在行上供 UI 归因）与 `GET /{team_id}/files`（共享目录的**用户入口**，此前不存在：
`_shared/` 是 agent workspace 的 sibling，workspace 浏览器看不见它）。两者复用既有 owner 校验。

**`_wipe_team_data` 必须同步删索引**：清理会 rmtree 掉共享目录，行若留下，面板照样列出这些
文件，用户要等到下载失败才发现——**留下孤儿行比不显示更糟**。团队 artifact 指向的正是被删掉
的那棵树，所以一并删除，连同它们的归因行（否则 history 表堆积永远无人读取的孤儿）。
过滤条件是**这个 team**，不是这个 owner：私有 artifact 与其他 team 不受影响。

## 2026-07-31 — idle carries started_at; messages carry event_id

Two serialization fixes for the roster/transcript:

- **idle branch now includes `started_at`.** The roster's "ran Ns" derives
  from started_at→finished_at; only running/stalled carried the start, so
  every finished turn rendered as a confident "ran 0s" while the DB held the
  real value (2026-07-31 issue, Step 3).
- **each chat message includes `event_id`** (from `BusMessage.event_id`,
  stamped by whichever path posted it — the trigger's in-turn room post or
  the agent's own bus send) — drives the per-message
  "view reasoning & tools" disclosure in the transcript. Null for user
  messages and legacy rows.

## 2026-07-30 — activity payload carries `event_id`

Every branch of `_member_activity` that has an activity row now also
surfaces `row["event_id"]` — the `events` row of the member's current/most
recent turn (written by `TurnActivity.note_event_id`). The frontend roster
uses it to fetch the finished turn's full event_log through the EXISTING
`/agents/{agent_id}/event-log/{event_id}` endpoint; no new route.

## 2026-07-28 — four-state activity payload, UTC-marked timestamps

`get_team_chat`'s inline activity block became `_member_activity`, and grew the
state the UI was missing:

- **`stalled`** is now distinct from `queued`. A turn that started and then went
  quiet used to be reported as queued, so a wedged worker and a busy room looked
  identical — nobody went looking. Carries `last_signal_at` for "silent for N".
- **`queued`** carries `queued_count` / `queued_since`, so the UI can say how
  long and how many instead of showing a bare word.
- **`idle`** keeps the previous turn's `steps` + `finished_at`, so a room can
  show what an agent just did.
- pending detection moved to [[local_bus]]'s batched
  `get_room_pending_summary` — the per-member loop was ~30 queries per 3s poll.
- the response carries `lead_agent_id` (the default responder) so the room can
  name who answers an un-addressed message; `thinking` is gone (the activity
  list supersedes it — 铁律 #2, no compat shims).

**Every timestamp now goes through `format_for_api`.** The local `_to_iso` was a
bare `.isoformat()` producing no timezone marker, so the browser parsed stored
UTC as local time: group-chat messages and the activity bar's elapsed ran an
hour early for a UTC+1 user, while 1:1 chat (which already used
`format_for_api`) was correct. `_to_iso` is deleted; this route was the last
one in the project not using the shared helper.


## 2026-07-22 — PR #141 review hardening (attachments + wipe + layering)

Three changes from review:

- **Echoed attachment dicts are no longer trusted.** ``send_team_chat`` used
  to store the client's whole dict after only validating ``rel_path`` — an
  open client-writable JSON channel into ``bus_messages.attachments`` and
  (via ``transcript`` in ``build_bus_markers``) raw text into the team
  prompt. Now ``_sanitized_attachment`` uses the echoed ``rel_path`` ONLY to
  locate the file, then reloads the dict the upload endpoint persisted
  server-side (``store_bus_attachment_meta`` → ``load_bus_attachment_meta``
  sidecar, see [[_bus_attachment_impl]]); no sidecar → minimal metadata
  rebuilt from disk, never a client transcript.
- **MIME sniffing consolidated.** The local ``_sniff_upload_mime`` (which
  returned libmagic's octet-stream verdict directly, diverging from the
  other two copies) is gone; the upload endpoint calls the shared
  [[mime_sniff]] helper. ``store_bytes_into_bus`` is now awaited (its disk
  write moved off the event loop).
- **Wipe without N+1.** ``_wipe_team_data`` deletes ``bus_message_failures``
  with one IN-subquery statement (bare identifiers, dialect-portable) instead
  of pulling every message_id into memory and deleting row-by-row inside the
  open transaction.
- Imports go through the public facades ``message_bus.attachments`` /
  ``message_bus.activity`` instead of the private impl modules.

## 2026-07-20 — team-chat messages carry attachments

`get_team_chat` now includes `attachments` (from `BusMessage.attachments`) per message, so
files an agent sent/shared into the room render in the group chat. Bytes are served by the
shared endpoint `GET /api/agent-inbox/attachments/raw?path=<rel_path>` (see [[inbox]]);
teams.py doesn't add its own serving route.

## 2026-07-22 — clear team data (counterpart to agent wipe)

New `DELETE /api/teams/{team_id}/data?chat=&files=` (owner-only) + `_wipe_team_data`. The
team analog of `wipe_agent_data`: clears the collaboration *surface* but KEEPS the team,
members, and the bus channel + membership. `chat` → delete `bus_messages` (+ their
`bus_message_failures`) for the team room channel (`created_by='team_<id>'`); `files` →
`shutil.rmtree` the `_shared/teams/{team_id}` dir. DB deletes in a transaction (commit
first), disk delete best-effort after. Idempotent (no room / no dir → zeros, no error).

## 2026-07-21 — default responder (no-@mention messages)

A team message with NO @mention used to trigger nobody (team rooms have a non-agent
`created_by`, so no member is the always-activated owner → silence). Now `send_team_chat`
routes an un-addressed message to exactly one agent via `_resolve_default_responder(team,
members)` = `team.lead_agent_id` if it's a current member, else the earliest-joined member
(`list_members_by_team` is ordered by `joined_at`). A single-agent team therefore
auto-responds; the picked agent can @-delegate. `update_team` (PATCH) sets/validates the
lead — a non-empty value must be a member; `""` clears it (exclude_none drops null, so empty
string is the "clear" wire signal). New nullable `teams.lead_agent_id` column.

## 2026-07-21 — voice input (parity with single-agent chat)

The upload endpoint gained a `source` query param and, for `audio/*` uploads, runs
`TranscriptionService` (Whisper) — same as the single-agent path — so @mentioned agents
receive the spoken words via the attachment marker (they can't listen). `agent_id=""` is
passed to `transcribe` because a team memo has no single agent; the NetMind signed-URL path
resolves the file via the shared-area fallback in [[transcription_public]]. `transcript` /
`source` land on the bus-attachment dict; the response echoes `transcription_available` so
the composer can show a "voice unavailable" notice. OpenAI-backend transcription reads the
local shared file directly (no fallback needed).

## 2026-07-21 — USER can attach files to a team message

New `POST /{team_id}/chat/attachments` (multipart) stores a user upload into the sender's
shared bus area via `store_bytes_into_bus` ([[_bus_attachment_impl]]) after team-ownership
check + server-side MIME sniff (`_sniff_upload_mime`, libmagic→ext→client) + size cap
(`backend.config.settings.max_upload_bytes`), returning a bus-attachment dict.
`TeamChatSendRequest` gained `attachments: list[dict]`; `send_team_chat` re-validates each
via `resolve_shared_file_for_user` (reject tampered rel_path), allows an attachment-only
message (empty content OK when files present), and passes them to `bus.send_message`. So a
human upload flows the same path as an agent-attached file (same shared area, same Read).

## 2026-06-23 — team group chat（基于 message bus，无 schema 迁移）

新增两个端点，把"团队群聊"叠在现有 message bus 上：
- `POST /:id/chat/messages`：用户以合成发送者 `usr_<user_id>` 发言，
  `mentions` 带 agent_ids（UI 的 `"@all"` → bus `"@everyone"`）。
- `GET  /:id/chat/messages`：返回转录（`usr_…` 解析为用户名）+
  `thinking`（在本房间有未处理 @ 的成员 → 前端 "…" 输入指示）。

`_get_or_create_team_room` 把团队映射到一个 group channel,并把
`created_by` 改写成非 agent 标记 `team_<team_id>`:既能确定性地找到房间
(不加列),又保证没有"房主 agent"被 MessageBusTrigger 无条件唤醒——
投递纯靠 @。成员每次同步到团队当前 agents。回复由独立的
MessageBusTrigger 在服务端产生(见 `message_bus_trigger.py.md` 的 team
分支),前端只轮询这两个路由。`TEAM_ROOM_OWNER_PREFIX` /
`USER_SENDER_PREFIX` 与 trigger 保持同步。

## 2026-05-13 — local 多用户隔离修复

`_user_id_for_request` 改成走统一 helper
`backend.auth.resolve_current_user_id`——cloud / local 共享同一条
路径，差异在 middleware 内消化。之前 local 模式 fallback 到
singleton "first user" 导致所有 local 用户 owner 相同、teams 互相
可见。详见 `auth.py.md`。

# routes.py — REST routes for team membership (subproject 1)

`/api/teams` CRUD + `/api/teams/:id/members` add/remove。

## 为什么存在

把 `TeamRepository` / `TeamMemberRepository` 暴露给前端 `TeamManagementModal` + `TeamFilterBar` + bundle export wizard。

## 设计决策

### 权限模型

每个端点都用 `_user_id_for_request(request)` 拿 user_id。它**两种形态共用一条路径**：`narranexus.sdk.web.current_user_id(request)`，因为 `auth_middleware` 在 cloud（JWT）和 local（`X-User-Id` 头）两边都会把身份写进 `request.state.user_id`，下游过滤逻辑因此完全一致。（曾经写过的 local 分支 `get_local_user_id` 已不存在，别照着去找。）所有 team 操作必须 `team.owner_user_id == request_user_id`，跨用户操作返回 403。

`POST /:id/members` 还要校验 `agent.created_by == request_user_id`（不能把别人的 agent 加进自己 team）。

### 删除 team 不删 agents

`DELETE /:id` 只 cascade 删 `team_members` 行，agents 本身保留。

## Gotcha

- 没做 `is_public` 维度（公开 team 给别用户加自己 agent），v1 不做。

## 2026-07-22 — get_team_chat returns per-member activity

`get_team_chat` now returns `activity: [{agent_id, status, phase?, tool_count?, started_at?}]`
alongside `thinking` (kept for back-compat). status = running (from [[_bus_activity]]
`is_live`, with live phase + elapsed) / queued (pending @mention, not yet running) / idle.
Drives the team status strip + activity bubbles.

## 2026-08-11 — 公告栏子资源 + `clear_bulletin` scope

新增 5 个端点（list / create / patch / delete / 按 tier 清空）。预算规则**不在这里**，
在核心包 [[team_bulletin]]：MCP 工具要强制同一套上限，而核心包反向 import 一个 FastAPI
路由会把分层倒过来。第一版就是那么写的，提交前修正。

`clear_bulletin` 是**独立 scope，绝不并入 `clear_chat`**。公告栏之所以存在恰恰因为它不是聊天；
并进去就意味着「清掉一段吵闹的 transcript」会静默销毁团队被交代过的每一条规则——
把用户直接送回公告栏本来要终结的那个复读循环。默认关。

`delete_team` **会**带走它，理由与工作台相同：team 行一没，公告栏唯一的读者
（这个团队的 prompt 构造器）也随之消失，剩下的是任何查询路径都读不到的孤儿行。

条目查找按 **(team_id, entry_id)** 而非仅 id：同一 owner 另一个团队的 entry_id
不该能从这个团队的路径上打开。

## 2026-08-11 (review) — 通知助手移出，两个未用 import 清掉

`_post_bulletin_notice` 移到核心包 [[team_bulletin]]（agent 写入也需要它，而核心包不能反向
import 路由），这里改为 import。`Optional` / `BulletinUsage` 在预算函数搬走后成了未用 import，
已删。

## 2026-08-10 — the work-board endpoint stopped writing its own SQL

`GET /teams/{id}/work-items` briefly carried a hand-written `SELECT` so it could
show `paused` items alongside active ones. It now calls
`TeamWorkItemRepository.list_visible`.

The filtering itself was never the question — it has to happen in SQL, since the
panel polls this endpoint every 5s and a long-lived team's `done`/`cancelled`
history only grows, so reading it all to discard most of it scales with the
team's age. What moved is WHERE the statement lives: keeping it in the
repository leaves the feature with a single dialect surface to test, instead of
a second raw statement in the route that the MySQL suite would have to grow a
reason to reach into.

## 2026-08-10 — 工作板端点直接用实体

`list_visible` 返回的是 `List[WorkItem]`,端点原先又 `model_dump()` 拍回 dict
再按字符串 key 取回来,把上一轮改动刚拿到的类型直接扔掉:`r["item_id"]` 拼错要
到请求时才炸,`i.item_id` 在 pyright 就拦得住。

## 2026-08-11 (review 收口) — 默认应答者规则与房间前缀改为 import

`_resolve_default_responder` 变成一层薄委托，实现移入 [[team_schema]]，好让总结 worker
用同一条规则而不是自己再写一份。两个房间前缀同样改为 import。

## 2026-08-11 (review 收口 2) — 删掉转发壳

`_resolve_default_responder` 上一轮变成了一层同名私有壳，只为了不改两个调用点和一个测试
import——那是兼容层，违反铁律 #2。壳已删除，调用点直接用 [[team_schema]] 的实现，
测试也改为 import 核心包那一份，这样那 5 条断言测的是**两个消费者真正共用的那份**，而不是壳。

## 2026-08-12 — 合成的 mention 记上来历;改名回写房间

**不取消合成。** team 房间靠合成 owner marker 关掉了"owner 全激活",没有 mention 就
没人被激活 —— 取消它房间就不应答了。所以路由行为一行未动,只是把「这个 mention 是
路由补的」写进 `routed_by`,让 trigger 能说真话。

**改名回写 `bus_channels.name`。** 房间自己存了一份名字,而**那一份才是 agent 看到
的**(`Your Channels` 渲染的是它)。此前改名只落 teams 表,于是每个成员继续把旧名字
念给用户听,而 UI 显示新名 —— 两边对不上,谁也解释不了。best-effort:改名本身已经
成功,回写失败只记警告。

## 2026-08-12 — `get_team_chat` 透传 `segments`

API 不返回的列，UI 就渲染不了。

## 2026-08-12 — 成员与 lead 变更落墙

`add_member` / `remove_member` / lead 变更三处宣告。**只在房间已存在时**宣告：
一个聊天从未打开过的团队没有 channel，为了叙述一次成员编辑而创建 channel 是本末倒置。

lead 只在**被设置**时宣告；清空 lead 是把责任按规则交回最早加入的成员，
那不是一个有名字可报的事件。

## 2026-08-18 — 私有的 `_get_or_create_team_room` 退役，改调 `team_rooms`

本文件里那个 30 行的私有 `_get_or_create_team_room` 已删除，四个调用点改调
[[team_rooms.py]] 的 `get_or_create_team_room` / `primary_room_of` / `team_room_marker`。

**理由不是「抽公共函数好看」，而是这个查询当时有四份副本。** `team_rooms.py` 的模块 docstring
把那段历史写清了：同一个「团队 → group channel」的映射分散在多处各自演化，而
「两个名字一个查询」正是四份副本的起点。本文件这一份是其中之一，是最后被收回的。

上面 2026-06-23 那条条目描述的是**当时**的私有实现，故意保持原样 —— 那是它当时确实的样子。
需要知道「团队房间的查找现在在哪」的读者应看本条与 [[team_rooms.py]]，而不是那一条。

## 2026-09-04 · builtin.teams as a feature-level plugin (batch 3c.2)

The router is no longer included by `backend/main.py`; it ships as the `ROUTES` contribution of the `builtin.teams` manifest (`backend.routes`, prefix `/api/teams`). Disabling the plugin in registry.json makes every `/api/teams/*` path 404 with no code path referencing teams.

Batch 6b.3: moved from `backend/routes/teams.py` into the builtin.teams package.
