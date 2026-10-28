---
code_file: plugins/builtin.channels.narramessenger/src/narranexus_plugins/narramessenger_module/matrix_trigger.py
stub: false
last_verified: 2026-10-16
---

## 2026-10-16（二）— 托管 DM 有三个成员：先认 `ai.netmind.room_creation.v1`，计数时排除精确的 `@hybrid-im-bot`

**现象（#415 上 prod 后）**：4/4 条消息（含 owner 房间）都是 `CLASSIFY target=group_silent`，agent 谁都不回。
**根因**：2026-09-16 起 NarraMessenger 每个托管房间都多一个已加入成员——Synapse appservice 机器人
`@hybrid-im-bot:<server_name>`（会议服务的发送方），agent 入房约 1 秒后加入。真实 1:1 房间 joined 成员是 3，`count == 2`
永远不成立。#415 之前 owner 房间能回，只是旧的部分计数器碰巧停在 2。

**房型规则（与 NarraMessenger 自家 runtime 一致，按优先级）**：
1. 房间状态事件 `ai.netmind.room_creation.v1`（state_key `""`），content 的 `room_type` 为 `dm` / `group` 时直接决定：
   `dm` → 不看人数一律 dm；`group` → 走提及规则（2 人也一样）。
2. 没有该状态 → joined 成员数**减去精确等于** `@hybrid-im-bot:<server_name>` 的那一个，`== 2` → dm，其余走提及规则；
   未知（0）且未提及 → group_silent（fail-closed 不变）。`server_name` 取 agent 自己 MXID（`credential.matrix_user_id`）
   冒号后的部分，所以国际服/CN（`matrix-cn.narramessenger.cn`）/staging 各自排除本服的机器人。只做精确匹配：
   `@hybrid-im-bot-evil:…`、`@hybrid-im-bot:别的服` 都算真实成员。MXID 没有服务器部分时不排除任何人（只会把房间看大）。
   NarraMessenger 规则里的第 2 档 `m.direct` account data 没实现。

**实现**：
- `_room_kind`（room_id → `dm`/`group`）+ `_room_kind_checked`（这个房间的创建状态已有确定答案：sync 见过、REST 取到、
  或 404）。来源两条：
  - sync 循环：nio 把这个自定义状态类型解析成 `UnknownEvent`；`connect()` 在调 `_apply_member_event` 的同两处
    （state 块、timeline）调 `_apply_room_creation_event`。只认 `state_key == ""`；后来的覆盖先前的；`room_type`
    不是 dm/group 时清掉 kind（回落计数），不保留旧值。
  - REST：`_resolve_room_shape` 先 `_get_member_count`（名单权威 + 回填显示名，逻辑未改，返回的仍是**含机器人的原始**
    人数），再 `_ensure_room_kind`：已有确定答案就不查；否则 `client.room_get_state_event(room, type, "")`。
    只有「HTTP 2xx（`transport_response.status`）+ `RoomGetStateEventResponse` + content 是不含 `errcode` 的 dict」
    才算查到 → 记 kind。nio 只把 404 映射成 `RoomGetStateEventError`；403/429/5xx 返回的是
    `RoomGetStateEventResponse`，`content` 是错误体（如 `{"errcode":"M_FORBIDDEN",…}`，502 是 `{}`），所以不能只看类型。
    404（HTTP 404，或 `RoomGetStateEventError` 且 `M_NOT_FOUND`）→ 记为已查、kind 未知；其他一切（错误体响应、
    其他错误、抛异常）→ 什么都不记，打 warning，下一条消息再查（名单权威不受影响，不重查 joined_members）。
    请求在途时 sync 先写进了创建状态 → 以 sync 的为准，不覆盖；请求在途时该房 `_drop_roster_authority` 过
    （`_room_kind_gen` 计数变了）→ 响应早于撤销，丢弃不记。名单查询失败时也照样查创建状态：声明为 dm 的房间名单
    查不到也判 dm。
- `_drop_roster_authority`（自己 agent leave/ban、`connect()` 自动入房成功）同时清掉 `_room_kind` / `_room_kind_checked`
  并递增 `_room_kind_gen`，下次分类重新读创建状态，在途的旧响应作废。
- `_classify` 新增 `room_kind=` 参数；`member_count=` 语义改为**有效人数**（已排除机器人）。`member_count` 为 None 时
  在函数内 `_resolve_room_shape`，并忽略传入的 `room_kind`。`_process_message` 用 `_resolve_room_shape` 一次拿到
  `(room_kind, 有效人数)` 传给 `_classify`；`CLASSIFY` 日志多打 `room_kind=` 和 `members=`（有效人数）。
- `parse_event` 的预分类房型改用 `_hinted_private(room_id, raw["_our_user_id"])`：已知 kind 说了算，否则（可能不完整的）
  名单去掉机器人后 `== 2` 才是 PRIVATE。`_reconcile_room_shape` 照旧在分类后回写。
- `_known_member_count` 仍是原始人数（含机器人），只给 `_get_member_count` 返回用；房型判断一律走
  `_effective_member_count` / `_hinted_private`。
- `_room_member_mxids`（authorize-event 的 `memberMatrixUserIds`）**不变**：完整名单，含机器人（NarraMessenger 要求）。
- **语音档位按有效人数，不按房型（PR #416 review I-1）**：路由按房型（创建状态 dm 压过人数），但 RTC 语音元数据没有
  来源绑定、创建状态的发送者也不校验，所以 `voice_instructions` 注入面仍只开给**有效人数恰好为 2** 的房间：
  - `parse_event`：`_hinted_voice_one_to_one` = `_hinted_private` **且**已知名单去掉机器人后 == 2，才调 `_detect_voice_turn`。
  - `_process_message` 的 dm 分支：`voice_one_to_one = (有效人数 == 2)`（未知 0 也关闭）；不成立时先用
    `_strip_voice_stamp` 撤掉 parse_event 按部分名单打的 `rtc_voice`（正文还原 `raw["body"]`，`chat_type` 不动），
    再 `_late_voice_upgrade(authoritative_dm=voice_one_to_one)`。冷名单的 {agent,user,bot} 首条语音照旧补升级。
  - 结果：声明为 dm 的多人房按 PRIVATE 路由、正常回复，但是纯文本、没有语音档位——有意的保守方向。
  - `_reconcile_room_shape` 的群判定撤语音戳改走同一个 `_strip_voice_stamp`（行为不变）；dm 判定时不撤，交给 dm 分支。
- `_classify` 传了 `room_kind` 却没传 `member_count` → `ValueError`（否则 `room_kind` 会被函数内重新解析悄悄丢掉）。

**测试**：`test_matrix_room_shape.py` 末尾新增一组（{agent,user,bot}→dm 且以 PRIVATE 进管线、owner 房间→dm、
三种冒牌机器人不排除、CN 服排除本服机器人、创建状态 dm 压过 4 人 / group 压过 2 人、sync state 块里的创建状态
不触发 REST、timeline 补来的创建状态生效、非 404 失败下条重试（抛异常，以及 403/502/429 错误体响应三例）、
在途撤销权威后响应不记、非空 state_key 的创建事件被忽略、非法 `room_type` 清掉已记 kind、声明 dm 的 4 人房
路由 dm 但无语音、parse_event 按部分名单打的语音戳在核验后被撤、parse_event 语音门要求有效 2 人、{agent,user,bot}
冷名单语音补升级、`_classify` 参数契约、authorize 载荷含机器人、parse_event 两例、
撤销权威后重读 kind、名单失败且无创建状态仍 fail-closed）。`test_matrix_classifier_and_cache.py` 的
`_seed_verified_roster` 同时把房间记进 `_room_kind_checked`（「已核验」现在也包括创建状态），否则 SimpleNamespace
假 client 会被调 `room_get_state_event`。

**已知残余（记录不实现）**：声明为 dm 但有效人数不是 2 的房间拿不到语音档位，按纯文本回复（PR #416 review I-1
的有意取舍）。创建状态的发送者不做校验——房间里有足够权限发状态事件的成员可以把群声明成 dm，
让 agent 在群里自动回复；authorize-event 仍由服务端复核。`_room_kind` / `_room_kind_checked` 与其他按房间结构一样
（及 `_room_kind_gen`）没有淘汰。被 redact 的创建状态事件不会清掉已记的 kind。`_classify` 在人数未知（0）但被提及时返回 group_mention（沿用原代码行为）。

## 2026-10-16 — 新建 1:1 房间被判成 group_silent：房型改为成员集合 + 每房一次权威 `joined_members`

**现象（prod 日志）**：官方公开 agent 被用户加好友 → NarraMessenger 新建 1:1 房间并邀请 agent → sync 循环自动入房 →
用户第一条消息 `CLASSIFY target=group_silent (… mentioned=False)`，只记忆不回复。老房间、或重启后首次出现且 sync 里没有
成员事件的房间走 REST 查成员，判 `dm` 正常。

**根因**：`_room_member_count` 是流水账计数器（进入 join +1、离开 join −1、未见过的房从 0 起），而 `_get_member_count`
缓存优先——任何成员事件写过一个数以后就再也不调 `joined_members`。新入房后的第一次 sync 只带部分 `m.room.member`
事件（常常只有 agent 自己的 join），缓存卡在 1，`count == 2` 永远不成立，房间在进程重启前一直当群处理。docstring
声称的「幂等」也不成立：同一 join 事件回放两次（state 块 + timeline、since 回退）会计两次。

**修法**：
- `_room_joined: dict[room_id, set[mxid]]` 取代计数器。`_apply_member_event` 按事件的**新** membership 改集合：
  `join` 加入；`leave`（含被踢）/`ban` 移除；`invite`/`knock` 不变；不再读 `prev_membership`。回放同一事件是空操作。
  空 mxid（`state_key`、`sender` 都空）的事件在函数开头直接返回，集合和显示名缓存都不写（PR #415 M-3：之前显示名会被
  记到 `(room_id, "")` 键下）。显示名缓存规则本身未动（join 带名写入、leave 删除）。
- 只由 sync 成员事件拼出来的集合**不权威**。`_get_member_count` 对每个房间（每进程）在第一次需要房型时调
  `joined_members`（每房每进程只需**一次成功**查询；并发 worker 可能各发一次，先核验成功的那份胜出），用响应**整体替换**集合、把房间记进 `_room_roster_authoritative`，并照旧回填显示名；之后直接返回集合
  大小，由成员事件持续增删维护，不再调 REST。
- **fail-closed + 重试**：REST 抛错、返回错误响应、或成员列表为空 → 返回 0（未知 → `group_silent`），**不**标记权威，
  下一条消息重试。即使 sync 已经攒出两个 join，REST 失败时也不信它（部分视图正是老 bug 的来源）。`_classify` 规则本身未改。
- **await 期间的竞态（预审 I-1）**：REST 在途时 sync 循环/其他 worker 仍在跑。
  - `_room_roster_gen[room]` 只在 `_apply_member_event` **真正改动**集合时 +1（回放已有成员不算）。`_get_member_count`
    在 await 前记下代数，返回后若代数变了 → 响应可能早于已应用的成员事件，丢弃并**立即重查一次**（总共最多
    `ROSTER_LOOKUP_ATTEMPTS = 2` 次；单次 REST 调用拆到 `_fetch_joined_members`）。两次都被打断才返回 0、不标权威
    （下一条消息再查）。不这样做的话，真实三人房可能被钉成 2（=dm，误自动回复），或 1:1 被钉成 3；有上限是为了
    成员不停变动的房间不会一直循环。
  - 同一房间两次查询重叠：await 返回后若房间已被另一次查询标为权威，就沿用那份（此后由成员事件维护）并返回其大小，
    不用自己这份（起得更早或同时、先后不明的快照）覆盖。REST 抛错时同样先看这一条。
  - **重新入房必须重新核验（可靠路径）**：`connect()` 自动入房分支在 `client.join(room_id)` 成功后撤销该房权威——
    离开期间的成员变动我们全没看到，旧集合已过时。join 失败则保持原状（邀请下次 sync 还会再来）：抛异常、或 nio 返回
    `JoinError` 响应对象（服务端拒绝如 `M_FORBIDDEN`、限流，nio 不抛异常）都算失败，打 warning、不打 auto-joined INFO、
    不动权威（PR #415 M-2）。
  - 我们自己的 agent（`_clients` 里各 client 的 `user_id`）`leave`/`ban` 也会撤销权威，但只是补充：自己离开的事件
    通常在 `resp.rooms.leave` 里，sync 循环不遍历它，只有当它出现在某个已加入房间的 timeline 里（比如同房还有我们的
    另一个 agent）才会生效。
- **房型回写（预审 I-2）**：`_process_message` 在 `_classify` 之后、语音补升级与 silent/完整路径分叉之前调
  `_reconcile_room_shape`：`dm` → `chat_type=PRIVATE`；任何群判定（含未知房型 fail-closed 的 group_silent）→ `GROUP`。
  下游按 `chat_type` 分支的地方（context builder 的 room_type、inbox 线程类型、熔断拒绝提示的群节流）因此看到的是
  权威房型，而不是 parse_event 按提示猜的。群判定时如果 parse_event 已按「部分成员表=2」打上 `rtc_voice`，一并撤销：
  去掉 `rtc_voice`，正文还原为 `raw["body"]`（RTC 元数据没有来源绑定，语音模式只限 1:1）。dm 方向仍由
  `_late_voice_upgrade` 补齐。

**PR #415 review（I-1）：没见过其 join 的成员离开**——非权威房间的集合可能是部分的，「不在集合里」分不清
「本来没 join」和「join 没同步到」，所以这种 leave/ban 不改集合、不递增代数，在途 REST 快照（还带着刚离开的人）会被
原样采信并钉成权威——例如只同步到自己 join 的新房间里，B 在查询途中退群，房间被永久钉成三人群（与 10-16 事故同症状）。
- 修法是**重查**，不是扣减：`_room_left_unverified[room]` 记录房间**非权威**期间出现的 leave/ban（不管集合里有没有）；
  同一 mxid 再 `join` 就移除。`_get_member_count` 每次 attempt 在 await 前和代数一起拍一份 `left_before`；响应回来后
  若 `当前离开者 − left_before` 非空（请求途中有人离开），就和代数变化一样作废这次响应、立即重查（日志与代数作废同形，
  文案是 "a member left while the lookup was in flight"，可区分）。重查时这条离开已在新的 `left_before` 里，不会再触发；
  两次 attempt 都碰上在途离开才返回 0、不标权威（与代数两次作废同语义）。只有在真正采纳名单时才清掉该房条目（权威后
  不再维护，避免第二份真相）。名单原样采纳，不做扣减，显示名照常回填。
- 为什么不扣减（PR #415 两轮 review）：扣减「请求前同步到的离开」是错的——之后拍的快照至少和它一样新；扣减「在途同步到
  的离开」也不总对——sync 投递有延迟，一条早于请求的离开可能在途才到，而那人在快照前又加回来了，快照正确地含他，扣掉
  就把三人群读成 2 人 = dm、在群里自动回复（fail-open）。重查让新快照自己反映离开和随后的回归，两种情况都不需要猜。
  请求前记下的离开（包括首次 sync state 块里的历史墓碑——sync 循环先应用 state 块再交出这一批的消息）不触发重查。
- 所有撤销权威的路径（自己 agent 的 leave/ban、`connect()` 自动入房）都经 `_drop_roster_authority`，同时清掉该房的
  离开者集合。这一步和 `join` 时的移除都只是记账（只有在途新增的离开才会作废查询，旧条目不影响结果）。我们自己的
  leave 不记入离开者集合（走撤销权威）。
- 没选「非权威房间的 leave/ban 一律递增代数」：代数不区分请求前/在途，首次 sync 的历史墓碑会被当成变动；用
  `left_before` 基线比较则只有在途新增的离开才算。

**PR #415 review（M-1）：首条 `@显示名` 提及**——`_process_message` 现在先 `await _get_member_count(...)` 解析房型（新房间
的首次查询顺带从 REST 名单回填显示名），再算 `mentioned`，然后把算好的数通过新增的可选参数 `_classify(..., member_count=)`
传进去，`_classify` 不再自己查第二次（查询失败时第二次调用会在同一条消息内悄悄重试）。生产路径（`_process_message`）
总是同时传 `member_count` 和 `mentioned`；两个参数为 `None` 时的回退只给直接测试分类规则的单测用（docstring 已写明）。
所以 agent 自己的 join 没同步到、只有 REST 名单里才有显示名时，新三人房的第一条 `@显示名` 消息就能判成 group_mention。

**同类消费面（已扫）**：
- `parse_event` 改读 `_known_member_count`（集合大小，权威与否都读；未知房为 0 → GROUP）。它只决定预分类的
  chat_type / 语音 1:1 门，权威 dm 判定后 `_late_voice_upgrade` 照旧补齐，行为与原来一致（只是数据源从计数器换成集合）。
- `_room_member_mxids`（authorize-event 载荷的成员提示）优先返回排序后的集合，集合为空才退回显示名缓存的键；仍只是提示，
  Narra 服务端自行复核。
- sync 循环的注释改正：state 块**不保证**是完整成员表。
- `SILENT_BYPASS_AUTHORIZE`、authorize-event 门、`_classify` 的三分规则均未改。

**已知残余（记录不实现）**：`parse_event` 打的 `chat_type`/语音标记仍是按提示猜的，只在 `_process_message` 里回写；
托管（Manyfold 转发）入口不经过 `_process_message`，没有成员数据，靠 `_authorize_event` 的「sender + 自己」兜底。
成员频繁变动的房间：两次查询都被打断时这条消息按未知房型（group_silent）处理，要等到某条消息的查询碰上安静窗口
才能判出 dm——fail-closed，但会有延迟。不经邀请、直接被加回房间的情况不会经过自动入房分支，只能依赖上面那条
自己离开的事件。按房间累积的进程内结构都没有淘汰（PR #415 review M-4，延后）：`_room_joined`、
`_room_roster_authoritative`、`_room_roster_gen`、`_display_name_cache`、`_room_left_unverified`（最后这个在核验成功或撤销
权威时会清，但从没收到过消息的房间只进不出）。将来做淘汰时成员集合和权威标记必须一起淘汰。

PR #415 这一轮的测试：`test_unseen_member_leave_during_lookup_triggers_refetch`（I-1：在途离开 → 重查 → dm、2 次调用）、
`test_leave_then_rejoin_within_lookup_keeps_the_member`（在途离开但快照正确含该人 → 重查后仍是三人群；扣减实现下变 dm）、
`test_leave_in_flight_on_both_attempts_fails_closed`（两次都有在途离开 → 0、不标权威）——这三条在去掉在途离开的 `continue`
时都会变红；`test_leave_synced_before_lookup_is_not_subtracted`（请求前的离开不触发重查、1 次调用、保留该人）、
`test_initial_sync_leave_tombstones_do_not_block_verification`（state 块墓碑先于查询应用，1 次调用核验完成）、
`test_rejoin_after_unverified_leave_is_not_subtracted` / `test_auto_join_clears_stale_unverified_leaves` /
`test_own_leave_clears_unverified_leaves`（行为断言：这些场景下名单不被削小；在途窗口生效后它们不再依赖记账清理）、`test_first_message_displayname_mention_uses_rest_backfill`
（M-1）、`test_join_error_response_keeps_authority`（M-2）、`test_member_event_without_mxid_caches_nothing`（M-3）。

锁：`tests/narramessenger_module/test_matrix_room_shape.py`（真实 `_apply_member_event` / `_get_member_count` /
`_process_message`，假 nio 客户端控制 `joined_members`；回退到 origin/dev 实现时 37 条里 29 条变红。其中
`test_member_event_during_lookup_triggers_one_refetch` / `test_lookup_invalidated_twice_fails_closed`（重查一次 + 上限）/
`test_late_landing_lookup_does_not_overwrite_verified_roster` / `test_own_leave_drops_authority_so_a_rejoin_is_reverified` /
`test_auto_join_of_reinvited_room_drops_its_authority` + `test_failed_auto_join_keeps_authority`（跑真实 `connect()`）锁 I-1，`test_fresh_dm_reaches_pipeline_as_private` /
`test_group_with_dm_looking_partial_roster_reaches_pipeline_as_group` / `test_group_silent_buffer_receives_group_shape` /
`test_voice_stamp_from_partial_roster_is_undone_in_a_group` 锁 I-2）；`test_matrix_classifier_and_cache.py` 的回放用例改为断言计一次。

## 2026-10-14（预审后）— 去掉空操作的 `_on_transport_connected` 覆写；关→开不回答积压

- 删除上一版对 `_on_transport_connected` 的空覆写：`connect()` 总是在第一次 sync 前给该 agent 设 per-agent 基线，
  基类对全局基线的推进因此对本渠道不起作用（没有任何 Matrix agent 会读到它）。防串扰靠的是 per-agent 覆盖本身
  （`test_another_agent_connecting_without_a_cursor...` 现在让 B 先连上、A 再冷绑，B 随后收到旧积压；把覆盖退化成共享值即变红）。
- **禁用→启用**：不管行怎么变成禁用的（用户开关、自动停用、bundle 导入落 `enabled=0` 带源环境游标、存量禁用行），回到启用时通用
  store 按描述符 `reset_on_reenable` 清掉游标，重新启用就是无游标首次 sync，禁用期间的消息被连接时刻基线丢弃
  （`ingress_dropped_historic`，带 `age_min`）。迟到的游标写入同样在迁移处被清。普通重连（无禁用）仍续传并处理积压。
  锁：`test_matrix_reconnect_replay.py` 的 `test_disable_then_enable_*` / `test_a_bundle_imported_*` / `test_a_late_cursor_write_*` / `test_plain_reconnect_*`。

## 2026-10-14 — 重连续传而非回放：游标从 DB 重读 + 历史过滤只用于无游标会话

两个叠加缺陷（线上 2011 条 historic 丢弃 = 186 个不同消息，最多重复 35 次；4 条真丢）：
A. `connect()` 用的是订阅者启动时的 `credential.matrix_since_token`，而活游标每次 sync 都写 DB——每次重连
   （prod 约 1–5 天一次「Matrix /sync failed: unknown error」）都重放订阅者启动以来的全部事件。
B. 基类重连时把**全 channel 共用**的 historic 基线推到 now，掩盖了 A 的重放（所以没出现双回复），但也把重连后
   送达、真正没处理过的积压当成「过旧」丢了。

修法（必须同时做，只修 B 会把 A 的重放变成大面积重复回复）：
- `connect()` 开头 `_resume_cursor`：经 credential manager 读 DB 里的最新游标；读失败/行不存在才退回凭据快照
  字段，并打 warning。
- `_set_session_baseline`：**无游标**（绑定后首次 sync/游标丢失）→ 该 agent 基线=本次 connect 时刻（沿用 5 分钟
  buffer），房间历史不被回答；**有游标续传**→ 基线 0，永不 historic 丢弃，至少一次的重叠由 DB 去重层
  （`channel_seen_messages`，键 `agent:event_id`）吸收，积压仍走 `ingress_processed` 审计。基线是 per-agent。
- 不覆写 `_on_transport_connected`：基类仍会在重连时推进全局基线，但每个 Matrix agent 在首次 sync 前都已有 per-agent 覆盖，全局值对本渠道从不被读取（见 [[channel_dedup_store]]）。
- **残余风险（记录不实现）**：停机很久后续传会回答全部积压；DB 去重保留 7 天，游标早于 7 天又写游标失败时可能
  重复处理。若需要「极旧积压」保护，应做成显式、可观测的策略，而不是静默 historic 丢弃。
锁：`tests/narramessenger_module/test_matrix_reconnect_replay.py`（真实 `_subscribe_loop` + `connect()`，仅假 nio 客户端）。

## 2026-09-11（PR #394 review 第五/六轮）— 拒绝提示改走基类钩子（流式与 atomic 两条路径）

覆写 [[channel_trigger_base]] 的 `_send_circuit_refusal` 为 `return await _send_matrix_reply(credential, message.chat_id, text)`
（返回 bool：homeserver 未接受时基类交还群聊窗口，下一条消息再试）。流式路径被拒时不自己发，只返回 `refusal`；
atomic 路径（`STREAMING_ENABLED=False`，含 kill switch 关掉时降级来的语音 turn）改调基类 `_run_agent_turn`，
`output.refused` 为真时直接返回文案、不再 `_send_matrix_reply`（第六轮 N-3：此前会双发且第二次绕过节流）。
两条路径的发送都只经基类 `_circuit_admission`，群聊房间与其他渠道一样每个熔断窗口只提示一次。
锁：`test_matrix_atomic_sends_a_refusal_once` / `test_matrix_atomic_group_refusal_is_throttled` /
`test_matrix_atomic_still_sends_the_agent_answer`。

## 2026-09-10（PR #394 review 第四轮 I-3）— 流式路径过熔断器闸门

`_build_and_run_agent_streaming` 用 `run_stream` 而不是基类的 `run_and_collect`，所以在
`run_stream` 之前自己调基类 `_circuit_admission`。本 channel 的 `send_channel_reply` 是基类 no-op，
被拒时拒绝文案经基类钩子 `_send_circuit_refusal`（本类覆写为 `_send_matrix_reply`）发回并返回；放行 → `run_stream(probe_token=)`
（[[client]] 按流的错误结论结算），消费循环的 `finally` 里 `_release_unsettled_probe` 兜底。
atomic 路径走基类方法，闸门随之生效（拒绝文案不由 atomic 再发，见 2026-09-11 节）。语音 drain 循环每个
批次各过一次闸门。锁：`test_matrix_streaming_refuses_a_paused_agent`、
`test_matrix_streaming_claims_an_open_window`。

## 2026-09-09 — `disable_credential(credential, reason="")`（B-28 I1）

基类契约变更：以关键字 `reason=` 调用并透传到 `disabled_reason`，写失败打 ERROR；缺 matrix 凭据的
自停用路径现在也带 reason（"missing matrix credentials … re-run the bind flow"）。

## 2026-09-07 — 宿主依赖改走 `narranexus.sdk.web`（批 6c，G2-I1）

批 6b 把 router 搬进插件包时，`from backend.*` 没跟着走：本文件当时还在 import 宿主的
私有模块（`routes._ownership` / `routes._mcp_egress` / `routes.dashboard.routes` 的下划线
函数）或非契约的公开符号（`backend.auth` / `backend.auth_errors` / `backend.config`）。
现在全部改成 `narranexus.sdk.web` 的 seam（实现见 [[plugin_sdk_host]]，形状见
[[web]]）。这不是改 import 路径的洁癖——第三方照抄这个 router 时，
`assert_owned` / `resolve_current_user_id` 一个都拿不到，只剩「自己重写鉴权」
（2026-08-12 那批 IDOR 的来源）这一条路。`pyproject.toml` 的
`plugins never import the host (backend)` 契约把这条线钉死，豁免列表为空。

## 2026-08-26 — `is_agent_peer` 覆写：从 MXID 读出来

平台把 agent 身份铸成 `@agent-<id>:<homeserver>`（见
[[_narramessenger_service.py]] 的 `_MATRIX_USER_RE`），所以在这个渠道
「对面是不是机器」是**前缀判断**而不是猜测。新增模块级常量
`AGENT_MXID_PREFIX` 承载这个拼法。

这是最需要它的渠道：它是唯一一个两个 agent 会常规地进行 1:1 对话、且房间
里没有别人的地方。

## 2026-08-24 — `is_agent_peer` + silent 分支补熔断

**`is_agent_peer` 覆写**：平台把 agent 身份铸成
`@agent-<id>:<homeserver>`（见 [[_narramessenger_service.py]] 的
`_MATRIX_USER_RE`），所以「对面是不是机器」在这个渠道是前缀判断而不是猜测。
新增模块级常量 `AGENT_MXID_PREFIX` 承载这个拼法。

这是最需要它的渠道：8/14 那 70 小时的乒乓就是两个 agent 在
NarraMessenger DM 里互相复读。答出这个问题同时收紧熔断阈值、关掉
「替它编一条回复」的 DM 兜底、并让 prompt 告诉模型可以闭嘴。

**`group_silent` 分支要单独挂闸门**：那条分支在
`super()._process_message` **之前** return，所以基类的闸门看不到它——但它
仍然会跑一条管线（记忆摄入）。群房里的复读风暴照样烧。dm / group_mention
两条路走 `super()`，继承基类那处即可。

## 2026-08-13（管线二审后）— 原文补发已摘除 + 认领键归一叶子名

①二审 Critical 实锤：`_send_matrix_reply` 的平文消息在通话上**会被 Hybrid worker
念给通话方**（冒烟报告里平文兜底轮有「首段延迟」=被听到；not-ok 兜底发的也是净化文
本，正因平文进语音管线）。因此一审建议的「原文补发」= 二次播报 + URL/代码逐字进
TTS，绕过 sanitize_for_tts 的结构层硬保证——**补发已整体摘除**。现契约：桥交付后
finalize 不再写房间（test_s1c 锁 `plain == []`）。已知代价：语音轮 narra_reply 里的
链接不落聊天记录，记 reference/self_notebook/todo/（等 Hybrid 给出 worker 消费面的
书面契约后再选载体：m.notice / rtc.* 键 / 仅 inbox）。`narra_reply_text` 的语义就此
定死为**净化后为空时的平文兜底捕获**，不是原文记录（二审 M#8 的字段两义已消）。
②认领键归一为叶子名（`rsplit('__',1)[-1]`）——delta 与 PROGRESS 两个事件源拼写可能
不同，裸串相等会让完成 PROGRESS 挡掉同一工具后续 delta（一审 Minor#6）。

## 2026-08-13 — 语音流认领制：narra_reply 也可驱动 live 交付

8/13 通话实锤：voice prompt 指令再硬（"ONLY way…speak"），模型仍 12/14 轮
用 narra_reply 回复——首字被钉死在 finalize（25–90s），而裸上游首字仅 ~3s。
Owner 约束：fast mode 是通用能力，不接受按 channel 遮蔽工具。修法改为
**认领制（8/13 Opus 预审后收窄为只管 delta）**：首个 trigger 托管回复工具
（`speak`/`narra_reply`，精确裸名或 `__` 后缀，见 `_is_voice_reply_tool`）产出
delta 即认领（`voice_stream_tool`），**只有认领者的 AGENT_REPLY_DELTA 喂桥**——
防两工具 delta 交错。**完成文本（PROGRESS）则全部进桥**：VOICE prompt 教的
预告→答案两连调可能跨工具，答案必须播出（review Important #3）；同文双调由桥的
等值段去重消化。**legacy capture 同时全保留**（narra_reply_text）：finalize 在
spoken 非空时提前返回不会双投，而净化后为空的回复（纯 emoji/纯 URL）只有这条
平文兜底路（review Critical #2——认领版曾把它丢了）。narra_send 永不进桥（自带
room_send，桥接=双投）。行为差异（有意）：narra_reply 走桥后其文本以 **TTS 净化形态**
落房间（URL/代码被剥）——语音轮链接的正路是 narra_send（自投递、不净化），
与 VOICE 模板「链接发到聊天里」的指引一致。裸名工具形态（`narra_reply`）是
既有事件形态（test_matrix_streaming_reply 112/117 行明确双形态钉住），谓词
接受精确裸名或精确 `__` 后缀，atomic extract_output 同谓词。NexusPower 侧零改动：expressive 面本就
含 narra_reply，其 arg delta 一直在到达，此前只是被 `__speak` 后缀滤掉。
测试：test_voice_stream_wiring（认领矩阵+真桥双场景）、
test_l1_scenarios::test_s1b（全链路：RTC→voice_fast→narra_reply 流→live）。

## 2026-08-11 — 通话级串行 key 统一为 per-room（review Important #2）

`_run_voice_serialized` 的 key 从「`rtc_session_id`、缺省回落
`agent_id:room`」改为**无条件 `agent_id:room`**。理由：1:1 PRIVATE 门保证
一个房间同时只有一路 RTC 通话，房间即通话；而两级检测下同一通话的严格
turn（keyed on session）和 degraded turn（keyed on room）若走不同 key，
一次 metadata 校验失败就会裂成两条并发 drain loop → 两路 TTS 流交错。
per-room key 让同一通话的两级 turn 共用一条串行队列；不同房间（≠同一
通话）仍完全并行。2026-08-06 条目里的「per-rtc_session」描述自此过时。
连带后果（故意的取舍）：`_merge_voice_batch` 合并跨级 batch 时最新一条
的 metadata 整体胜出——degraded 尾巴会丢掉更早 strict turn 的
correlation ID、`[voice-timing]` 打成 `degraded`。correlation 永远跟
最新 turn，不做拼接。

## 2026-08-11 — voice 检测升级为两级（common trigger + degraded 打戳）

`_detect_voice_turn` 从「严格 v1 全对才算 voice」改为 handoff §3.4 的两级
契约：Level 1 = 严格 metadata 解析成功 → 完整 voice turn（四 correlation
ID + `degraded: False`）；Level 2 = 严格解析失败但 metadata 对象里仍有非
空白的后端控制 `voice_instructions` 字符串（`_rtc_voice.
extract_common_voice_instructions`）→ **DEGRADED voice turn**：语音行为
照常（剥 envelope、fast profile、voice bridge），但四个绑定 ID 一律置空
串且 `degraded: True`——空 ID **绝不能**喂给 correlation、通话级串行 key
或任何安全判断。两级都不中 → 普通文字消息（信封保留、正文不动）。

承重不变量：
- **1:1 PRIVATE 门现在对两级承担全部注入论证。** degraded turn 的
  metadata 本来就不可信，所以「level 1 校验过格式」从来不是放宽房型门的
  理由——parse_event 的注释块已改写成这个论证，别回退。
- 下游零改动即继承：`_run_voice_serialized` 统一按
  `f"{agent_id}:{chat_id}"` per-room key 串行（见上一条——两级 turn 共用
  一条队列）；`_voice_profile_for` / `_late_voice_upgrade` 只看
  `raw["rtc_voice"]` 真值/复用 `_detect_voice_turn`，自动两级。
- `[voice-timing]` 行在 degraded turn 上把 rtc_session 打成字面量
  `degraded`（原来是空串），让延迟样本能按级分桶。
- 空 transcript 丢 turn、streaming kill-switch 剥 rtc_voice 整轮降级两条
  规则对两级同样生效，未动。

测试：`test_voice_turn_detection.py` 翻转原「invalid → plain text」用例为
degraded 断言（degraded=True、四 ID 空串、envelope 剥离），新增「invalid
且 instructions 缺失/空白/类型错 → 纯文本、信封保留」钉子；valid 路径加
`degraded is False` 钉子。voice_sim 的 S5（钉旧降级行为）本改动后必红，
翻转归下一任务，不在本 commit 处理。

## 2026-08-07 (二次) — 流式路径也认平台代投递的回复（review 收口）

`resolve_agent_response` 那层收口**到不了这条路径**：`_build_and_run_agent_streaming`
不走 `extract_output`，它返回流中捕获的 `state.narra_reply_text`，而该字段只从
`arguments["text"]` 取——step_3 兜底那帧的参数是
`{"content": …, PLATFORM_REPLY_TEXT_KEY: …}`，没有 `text` 键，于是被
`_handle_stream_event` 的空值守卫直接丢掉。后果：消息真的进了房间（
`ChannelSenderRegistry` 发的），但 `final_text == ""` → 走
`_finalize_stream_silent` → NO-OP，且基类按 `agent_response=""` 写 `bus_messages`
——**又一次「已投递却记成沉默」，偏偏发生在本改动自称「最尖锐的一例」的路径上。**

- `_StreamReplyState` 新增 **独立字段** `platform_reply_text`。**不复用
  `narra_reply_text`**：finalize 会把后者 fresh-send 进房间，复用就等于把已经投递过的
  消息**再发一次**。
- `_handle_stream_event` 在取 `text` 之前先认 `PLATFORM_REPLY_TEXT_KEY`，命中即记录
  并 return（不碰房间）。
- finalize 顺序：`narra_reply_text` → 发房间并返回；否则 `platform_reply_text` →
  **只返回、零房间写操作**（inbox 因此记到真实文本）；都空才走
  `_finalize_stream_silent`。
- `_finalize_stream_silent` 那行「no reply, no error」日志改成「agent 没回、平台也没代写」
  ——原措辞在平台已代投递时是**假的**，会把排障的人引向一个其实已被回答的轮次。

爆炸半径（诚实标注）：本渠道 `get_conversation_history` 返回 `[]`（历史由 homeserver
提供），所以**不会**出现微信那种「下一轮上下文里 bot 说了 (stayed silent)」的污染。
受影响的是 `bus_messages` 记录本身（Inbox / 历史展示及以该表为准的下游）与那行误导日志。

测试：`tests/channel/test_platform_reply_reaches_the_inbox.py` 的
`TestStreamingPathCapturesPlatformReply` 驱动真的 `_handle_stream_event`，断言
平台帧进 `platform_reply_text`、`narra_reply_text` **保持为空**（防双发的结构性钉子）、
organic `narra_reply` 不受影响、空白平台文本被忽略。

## 2026-08-07 — 流式路径不再手搓 trigger_extra_data

`_build_and_run_agent_streaming`（`STREAMING_ENABLED` 是**默认路径**）自己组
`extra_data`，于是错过了 2026-08-06 加的轮次信封：`channel_room_type` 恒为空 →
`step_3` 把 NarraMessenger 私聊全判成群聊 → **1:1 无回复兜底在本渠道是死代码**。
尤其讽刺的是，这条路径「无回复 + 无错误」的终局逻辑正是 NO-OP（房间不动），也就是
那个兜底存在的全部理由。

改为调 `ChannelTriggerBase.build_trigger_extra_data(..., builder=builder,
attachments=attachments)`；`rtc_voice` 仍在其后按原逻辑附加（它依赖本方法算出的
`turn_profile`，不属于公共键）。教训与 grep 级守卫见
`channel_trigger_base.py.md` 2026-08-07 条目。

## 2026-08-06 — voice 检测双入口 + atomic speak 投递（第三轮 review 收口）

**voice 检测现在有两个入口，这是本文件最新的承重结构**：①parse_event（热 roster 缓存、chat_type==PRIVATE 才检测）；②`_late_voice_upgrade`（`_process_message` 在 `_classify` 返回权威 dm 之后调用，专为冷 roster 缓存兜底——真实通话第一轮不再降级）。两入口共用纯函数 `_detect_voice_turn`，判据永不分叉。边界是函数自证的：`authoritative_dm` 形参强制调用方声明权威房型（不是靠调用点位置），接线由 test_late_upgrade_wired_only_on_dm_classify 钉住——把调用挪出 dm 分支测试必红。envelope-only 正文在两条路径上同规则**丢 turn**（late 路径返回 None，调用方 return——envelope 永远不能以用户输入身份跑 agent）。atomic 路径的 speak：extract_output 改为与 streaming 语义同构的有序折叠（speak 追加、narra_reply 置换），同一调用序列两条路径同一答案；「never silent ok:true」承诺自此对两条路径都成立。

## 2026-08-06 — auto review 收口（PR #247 两轮意见）

review 收口：①voice 检测限 1:1 房间（metadata 只验格式不验来源，群内成员可注入 voice_instructions；F13 本就是 1:1 通话；冷 roster 缓存在 parse 期保守拒绝、由 _process_message 的 late upgrade 补齐（见 08-06 第三轮条目））；②speak 在文字轮不再是 ok:true 的死工具——streaming 由 PROGRESS 捕获进 narra_reply_text，atomic 由 extract_output 有序折叠（两路径同答案，见 08-06 第三轮条目）；③speak 匹配谓词统一 endswith("__speak")；④STREAMING_ENABLED 同样约束语音——开关关闭时剥 rtc_voice 整轮降级普通文本，不半激活。

## 2026-08-06 — voice fast mode: 观测（voice-timing + profile 标记）

观测：[voice-timing] 纯函数行（handoff §9 六 timestamp 映射为自 received 起的时长，缺失戳 -1.00，只带非敏感 correlation ID 绝不带 transcript），finalize 处发射；戳位 = streaming 入口(received/applied)、run_stream 创建后(request)、bridge 的 first_delta/first_sent/finalized。

## 2026-08-06 — voice fast mode: 通话级串行（per-rtc_session）

_build_and_run_agent 在 rtc_voice 消息上先过 _run_voice_serialized：按 rtc_session_id（缺省 agent_id:room）一条 drain loop 串行；run 期间到达的语句缓冲并 _merge_voice_batch 合并成一次后续 turn（transcript 按序拼接、以最后一条的 rtc 元数据为基准——correlation 跟最新 turn_id）。不同通话完全并行；通话闲置即清 state。设计蓝本是 group_silent 的 per-(agent,room) 缓冲。非语音消息 dispatch 逐字不变（getattr 守卫，替身消息也安全）。

## 2026-08-06 — voice fast mode: VoiceDeliveryBridge 接线

_StreamReplyState 增 voice_bridge（None=文字 turn，所有 legacy 分支以 bridge is None 为守卫，行为逐字不变）。_handle_stream_event：speak 的 AGENT_REPLY_DELTA 喂桥、speak PROGRESS 作权威全文修正且不落 narra_reply_text、narra_reply 捕获在语音 turn 上照旧。finalize：bridge.close() 成功即完；finalized_ok=False 时 _send_matrix_reply 平文兜底；无 spoken 文本则回落 legacy finalize（narra_reply/错误标记/静默三态不变）。

## 2026-08-06 — voice fast mode: RTC 检测 + voice register + speak

parse_event 的 text 分支做 RTC v1 检测（_rtc_voice 严格校验，任一失败=普通消息）：剥 envelope、正文=transcript、raw["rtc_voice"] 带四 ID + 有效 voice_instructions（metadata 优先、envelope 兜底）；空 transcript 丢 turn。_voice_profile_for 把 rtc_voice 映射为一次性 TurnProfile.voice_fast()（不落 session）。streaming 路径把 profile 传 run_stream、rtc_voice 进 extra_data。

## 2026-08-04 — `matrix_since_token` 排除出熔断指纹

`BREAKER_VOLATILE_CREDENTIAL_FIELDS` 覆写加 `matrix_since_token`。该列每
收到一次 sync 响应就重写（凭据 dataclass 里明写 WRITE-HEAVY），留在基类
新增的快速死亡熔断指纹里，等于「本 channel 自己的正常流量」持续伪装成
owner 换绑——熔断刚跳闸就被判定为凭据已变更并清除，本 channel 相当于没有
熔断器。见 [[channel_trigger_base.py]] 8/4 条目。

## 2026-08-03 — `managed_before_run`:托管来源同样过 authorize 门

托管(Manyfold 转发)turn 复用原生 `_authorize_event`(fail-closed
语义原样:无凭据/传输错/非 2xx 全 deny);非 @ 群消息镜像
SILENT_BYPASS_AUTHORIZE 跳过门。deny 且带 server 建议 notice 时,
原生经 live client 发 m.notice——托管模式无 client,改用同凭据走
`matrix_room_send` 纯 HTTP。房间成员表在托管下为空缓存,
`_authorize_event` 已有的"至少含 sender + 自身"兜底覆盖此情形。

## 2026-07-20 — compound wire format changed: single `ai.netmind.compound` event

Real dev bug (agent_743423ca551b): a group @-mention carrying a
document/image attachment got NO reply — the whole message was silently
dropped at ingest. Root cause: **NarraMessenger changed how a
document/image attachment is delivered on the wire.**

- **Old (verified 2026-07-03, agent_62cf)**: two events — a hidden
  ``m.text`` whose ``content["ai.netmind.hint"].kind=="compound_trigger"``
  carried a ``compound_preview`` (real text + media mxc), plus an ignored
  ``ai.netmind.compound`` sibling. ``_wrap_event`` matched the ``m.text``
  (``RoomMessageText`` → isinstance hit) and lifted the preview.
- **New (verified 2026-07-20)**: a SINGLE event with the custom msgtype
  ``ai.netmind.compound``; the payload self-describes under
  ``content["ai.netmind.compound"]`` (``media_url`` / ``mime_type`` /
  ``file_name`` / ``size`` / ``text``) and the @-mention rides in
  ``content["m.mentions"]`` on the same event. The hidden hint ``m.text``
  is gone.

Why it broke: a custom msgtype has no nio factory, so nio parses the event
as ``RoomMessageUnknown``. The old branch gated compound detection inside
``isinstance(event, RoomMessageText)`` → the new event matched neither the
Text nor the Media branch → fell through to the ``skipping event`` no-op →
``None`` → dropped (0 ingress, no group_mention, no reply). Verified with
nio on the box: ``RoomMessage.parse_event`` on the real content →
``RoomMessageUnknown`` (has ``.source`` with the payload + ``m.mentions``).

Fix: ``_wrap_event`` pulls ``source.content`` up front and matches
``content["msgtype"] == "ai.netmind.compound"`` BEFORE the isinstance
branches, emitting the same ``m.room.message.compound`` dict downstream
already understands. One branch covers every attachment mime (pdf / image
/ video / …) — only ``mime_type`` differs; ``parse_event`` →
``_media_content_type(mime)`` classifies IMAGE/FILE/VIDEO/AUDIO. Because
the wrapped dict carries ``_nio_event`` (the RoomMessageUnknown, whose
``.source`` holds ``m.mentions``), ``_is_mentioning_us`` sees the @ and the
message classifies as **group_mention** — the whole point.

Scope check (evidence, not assumption — sampled the room on the wire):
PDF ``application/pdf`` and image ``image/jpeg`` BOTH arrive as
``ai.netmind.compound`` (same shape, different mime). A **voice note**
arrives DIFFERENTLY — as a standard ``m.audio`` (``RoomMessageMedia``,
payload under ``content.info``, NO ``m.mentions``) — handled unchanged by
the media branch; a group voice note without an @ is correctly
``group_silent`` (memory-only, no reply), not a bug.

Per 铁律 #2 the old two-event hint path is **removed**, not kept as a
compat shim (the server no longer emits it).

## 2026-07-09 (review fixes) — full progress removal + inline

PR #78 review round. Four cleanups applied on top of the 2026-07-08
refactor:

1. **`_handle_stream_event` no longer has a `narra_progress` branch.**
   The tool itself was deleted in the same PR (see
   [[_narramessenger_mcp_tools]] 2026-07-09), and a stray call from an
   older prompt now falls through as an unknown tool call — silent,
   no state change. Test: `test_stray_narra_progress_is_inert`.
2. **`_finalize_stream_with_reply` inlined.** The method had degraded
   to a single `await self._send_matrix_reply(...)` call, and its
   `state` parameter was unused. Removed and inlined at the sole
   caller in `_build_and_run_agent_streaming`. Only
   `_finalize_stream_silent` remains as a distinct finalize hook.
3. **Test coverage extended to the outer try/except path.** The
   belt-and-suspenders branch where `async for event in client_stream`
   raises a Python exception (rather than emitting
   `MessageType.ERROR`) had no test — that's exactly the path
   `LineTooLong` from the anyio 128 KiB readline limit percolates up
   through. New test
   `test_run_stream_exception_surfaces_error_marker` asserts the
   room receives `STREAM_ERROR_MARKER` in that scenario.
4. **`narramessenger_context_builder`'s `reply_instruction` no longer
   mentions `narra_progress`.** Prompt now tells the agent explicitly
   that the room stays quiet until `narra_reply` fires (see the
   [[narramessenger_context_builder]] 2026-07-09 note).

## 2026-07-08 (UX refactor) — no placeholder, silent = quiet room

Dev feedback: even after the 2026-07-03 fix (edit-to-`·`-marker instead
of redact), users flagged the "💭 Thinking… → ·" sequence for casual
messages ("ok"/"嗯"/etc.) as visually confusing. The agent appears to
"try to say something" then trails off — worse UX than staying silent.

**Explored & rejected**: deferred/lazy placeholder (send only if agent
takes >800 ms). RDS timing data (2026-07-08) shows our typical Matrix
turn is 20–60 s; the ONE fast case in ~30 days of dev traffic was 5.4 s.
No reasonable delay window skips the placeholder on the silent path
without also delaying it on the reply path.

**Decision**: kill the placeholder outright. The room stays as-is while
the agent works; the trigger fresh-sends when there's something to say.

Rules matrix (updated after 2026-07-09 review fixes):

| Situation                        | Room outcome                       |
|----------------------------------|------------------------------------|
| Agent calls `narra_reply(text)`  | Fresh `room_send` on finalize      |
| Agent stays silent (no reply)    | NO-OP — room untouched             |
| Runtime crash (ERROR / raise)    | Fresh `room_send` `STREAM_ERROR_MARKER` — failures MUST stay visible |

Constants surviving: `STREAMING_ENABLED`, `STREAM_ERROR_MARKER`.
Removed: `STREAM_PLACEHOLDER_TEXT`, `STREAM_PROGRESS_MIN_INTERVAL_MS`,
`STREAM_SILENT_MARKER`. Removed method: `_apply_progress` (and after
2026-07-09, `_finalize_stream_with_reply` was inlined too). Removed
imports: `matrix_room_edit`, `matrix_room_redact` (the `m.replace` and
redaction paths no longer trigger).

`_StreamReplyState` slimmed down: `placeholder_event_id`,
`last_progress_ms`, `send_failure` gone. Only `narra_reply_text`,
`error_seen`, `last_error_message` remain — the trigger no longer needs
to track any Matrix event ids across the stream.

The 2026-07-09 review round finished the job on `narra_progress`: the
MCP tool was deleted entirely (not just neutered), and the prompt no
longer instructs the agent to call it.

Underlying `LineTooLong` on oversized `tool_call_output_item` still
requires an upstream fix (Claude Agent SDK / anyio 128 KiB readline
limit). Filed as an agent_framework follow-up.

## 2026-07-03 (UX fix) — silent finalize: edit-to-marker, not redact

**Superseded by the 2026-07-08 refactor above** — kept for provenance.


Live incident on dev EC2 (`agent_3dbc1343a078`, room
`!nkPGwwuXRnhUBvJOlC:matrix.netmind.chat`) surfaced two overlapping
bugs the redact path hid:

1. **`aiohttp.http_exceptions.LineTooLong: Got more than 131072 bytes`**
   fatal-crashed the agent turn on `tool_call_output_item` outputs
   >128 KiB (Claude Code's `Read` on a PNG returned base64 that
   exceeded anyio's hardcoded line limit). Every turn ended
   `_finalize_stream_silent → matrix_room_redact` → room showed
   "message deleted" as the ONLY visible outcome. The runtime
   fatal was in the log but never surfaced to the sender.
2. Even for INTENTIONALLY-silent turns (agent chose not to speak),
   `redact` renders in every Matrix client as a prominent "message
   deleted" line — misleading enough to look like a bug.

Fix: `_finalize_stream_silent` now EDITS the placeholder (via
`m.replace`) instead of redacting. Two new class-level constants:

- `STREAM_SILENT_MARKER = "·"` — discreet dot for the "no reply
  intended" case. Keeps a single message thread in the room without
  the alarming delete indicator.
- `STREAM_ERROR_MARKER = "⚠️ Sorry — I hit an internal error and
  couldn't finish. Please try again in a moment."` — used when the
  runtime emitted a `MessageType.ERROR` (or the stream loop caught
  an exception) during the turn. Tells the sender to retry rather
  than assume they were ignored.

`_StreamReplyState` gains two fields the state machine writes at ERROR
time:
- `error_seen: bool` — set by `_handle_stream_event` when
  `MessageType.ERROR` fires, and defensively by the outer
  `run_stream` try/except so a Python-level exception (like the SDK
  transport's LineTooLong percolating up) also triggers the error
  marker path.
- `last_error_message: str` — truncated excerpt logged at finalize
  time (`last_error=...`) so live debugging doesn't need to re-open
  the log for the same turn.

Finalise fallback chain when a placeholder exists:
1. Try `matrix_room_edit` → success is the happy path.
2. If edit fails transient → try `matrix_room_send` of the marker
   (fresh message) so the sender still sees something meaningful.
3. If BOTH fail → attempt `matrix_room_redact` as last-resort
   cleanup (better a redact than a stranded "💭 Thinking…" in the
   room).

No-placeholder cases:
- Error + no placeholder: fresh-send the error marker (user must
  see the failure).
- Silent + no placeholder: no-op (room is already clean).

Follow-up (out of scope for this trigger fix): the `LineTooLong` at
128 KiB is a Claude Agent SDK / anyio bug — the SDK transport uses
`TextReceiveStream` on the CLI's stdout with anyio's default
readline limit. Bumping requires a monkey-patch on the SDK's
subprocess transport or an upstream fix. Filed as a follow-up on
the agent_framework side; the trigger just surfaces the failure
gracefully now.

Tests: `test_matrix_streaming_reply.py` gains
`test_finalise_silent_edits_placeholder_to_silent_marker`,
`test_finalise_error_edits_placeholder_to_error_marker`,
`test_finalise_error_no_placeholder_sends_fresh_marker`,
`test_error_event_sets_error_seen_flag`. The old
`test_finalise_silent_redacts_placeholder` is replaced (the redact
was the bug).

## 2026-07-03 (review fixes) — sync-loop robustness + mention scoping

PR #60 review round. Four correctness/robustness fixes:

- **`client.sync()` now checks `isinstance(resp, SyncResponse)`.** nio returns
  a `SyncError` object (not an exception) on a Matrix-level failure — a revoked
  token used to be read as an empty-rooms success and 120s-reconnect forever,
  so `is_permanent_auth_failure → disable_credential` never fired. On non-success
  we `raise RuntimeError(f"...{status_code} {message}")`; the base classifies the
  errcode (M_UNKNOWN_TOKEN/… → disable; else → backoff).
- **`AsyncClientConfig.max_timeouts` 0 → 1.** `0` = nio retries transport errors
  internally forever and never returns, so the base's backoff/reconnect (and its
  per-subscriber transport audit — the L2 liveness signal) never ran → silent
  zombie (incident #1/#4). `1` surfaces the error so the base owns retry.
- **`_is_mentioning_us` surface (3) is now room-scoped + `@`-prefix only.** It
  walked `_display_name_cache` across ALL rooms and matched the name as a BARE
  word (`name in body.split()`), so "I called the Agent" false-fired
  group_mention → a full agent run + group reply. Now uses only
  `(this_room, my_id)`'s cached name and requires the explicit `@name` form;
  raw MXID (2) and MSC3952 (1) remain the other two legitimate surfaces.
- **Silent-debounce `create_task` got an `add_done_callback`** (`_on_flush_task_done`)
  — a crash in `_debounce_flush → _flush_silent` was only a GC "never retrieved"
  warning (incident #2); now logged as ERROR.
- Minor: `_apply_progress` uses `get_running_loop().time()`.

Tests: mention regressions in `test_matrix_classifier_and_cache.py` (bare-word +
cross-room now group_silent).

## 2026-07-03 (redesign) — streaming is now "thinking + agent-driven progress"

Owner feedback: the token-delta streaming (below) read as jumpy — Matrix
`m.replace` fully redraws the body per edit, so debounced token chunks flicker,
and for non-Claude models the raw `AGENT_RESPONSE` output isn't clean answer
text anyway. Replaced the whole delta-streaming machine with an intentional-only
model:

- **Ship a `💭 Thinking…` placeholder IMMEDIATELY** at turn start (one
  `room_send`), before consuming the stream — instant feedback.
- **`AGENT_RESPONSE` deltas + `AGENT_THINKING` are now IGNORED.** The room shows
  only what the agent deliberately posts. No accumulation, no debounce, no char
  gates — those constants/fields (`STREAM_MIN_CHARS_BEFORE_PLACEHOLDER`,
  `STREAM_EDIT_DEBOUNCE_MS`, `STREAM_EDIT_MIN_DELTA_CHARS`, `accumulated_text`,
  `last_edit_ms`, `last_edited_length`, `_maybe_ship_or_edit`) are GONE.
- **New `narra_progress(text)` tool** (marker in [[_narramessenger_mcp_tools]]):
  the agent optionally calls it during long work; `_apply_progress` `m.replace`-
  edits the placeholder to that status, rate-limited by
  `STREAM_PROGRESS_MIN_INTERVAL_MS` (900ms floor vs Matrix's ~1 msg/s budget).
  The prompt (`narramessenger_context_builder`) tells the agent it's optional.
- **`narra_reply(text)`** → final `m.replace` overwrites the placeholder with the
  answer (fresh `_send_matrix_reply` if the placeholder never shipped). Silent
  turn → redact. Finalize methods unchanged.

Net: one message that shows `💭 Thinking…` → (optional agent status lines) →
final answer, updating in place. `_StreamReplyState` shrank to
`placeholder_event_id / narra_reply_text / last_progress_ms / send_failure`.
Tests rewritten in `test_matrix_streaming_reply.py`. The token-delta description
below is SUPERSEDED — kept only for history.

## 2026-07-03 (hotfix) — streaming looked for tool calls on the wrong event type

Second live-run bug in the streaming state machine: every turn ended with
`_finalize_stream_silent` redacting the placeholder, so the user's room showed
"message deleted" AFTER a streamed answer that got wiped out at the end. The
agent was in fact calling `narra_reply` (verified in the log:
`Tool call: mcp__narramessenger_module__narra_reply` at 15:54:16), but our
handler wasn't recognising it.

Root cause: `_handle_stream_event` matched on `MessageType.TOOL_CALL` and read
`.tool_name` / `.tool_input`. In this codebase, tool calls are actually emitted
as `ProgressMessage` (`message_type=PROGRESS`) with `details.tool_name` +
`details.arguments`. The `AgentToolCall` schema class exists but is never
constructed — see the pattern in `run_collector.py:143-165` which we now
mirror. State never captured `narra_reply.text` → finalise silent → redact.

Same-turn secondary regression from that bug: the "streaming felt too fast /
jumpy" complaint. Matrix `m.replace` fully replaces the body per edit, so
each ship causes a full client-side redraw. With debounce 700ms + 30-char
delta, the client saw 2-3 flashes per second. Bumped defaults to
1200ms + 80 chars — fewer, larger updates read as OpenClaw-smooth.

Handler now recognises:
- `MessageType.PROGRESS` + `details.tool_name` matches `"narra_reply"`
- `details.arguments` as EITHER dict OR JSON-encoded string (some SDK
  paths serialise mid-stream)
- MCP-prefixed name (`mcp__narramessenger_module__narra_reply`) via
  substring match, not equality — locked in a test so a future
  refactor to strict equality regresses loudly.

Also added an INFO log at capture time so a future silent-redact regression
shows up in the log with a clean "captured narra_reply.text" line to search
for. If the log lacks that line but the redact fired, the mismatch is
between the event shape and the handler — same failure mode as this bug,
easier to diagnose.

## 2026-07-03 (hotfix) — streaming crashed on a wrong ChannelTag import

First live run after enabling streaming: the agent stopped responding to
NarraMessenger messages entirely. `_build_and_run_agent_streaming` lazy-imported
`ChannelTag` from `channel.channel_prompts` (where it does NOT live) → `ImportError`
on EVERY message, AFTER receive/classify, so it read as "not receiving". Fixed to
`from narranexus.platform.schema.channel_tag import ChannelTag` (where the base and
Lark import it). Slipped through because the 12 streaming tests drive the state
machine sub-methods directly and never call `_build_and_run_agent_streaming`, so
its lazy import was never executed under test — a reminder that streaming needs a
live smoke, not just unit coverage. Event-field assumptions (`message_type` /
`delta` / `tool_name` / `tool_input`) were verified correct against
`schema/runtime_message.py` at the same time.

## 2026-07-03 (Phase 4) — progressive streaming via `m.replace`

New `_build_and_run_agent_streaming` path replaces the atomic single-
send with an OpenClaw-style live edit stream. Placeholder → debounced
edits from `AGENT_RESPONSE` deltas → final overwrite from
`narra_reply.text` when the tool call materialises. `AGENT_THINKING`
is intentionally ignored — the room shows the agent's final answer,
never its chain-of-thought.

Kill switch: `STREAMING_ENABLED = True` class-level constant. Flipping
to False routes through the preserved `_build_and_run_agent_atomic`
path (byte-identical to the pre-streaming behaviour) — used when
Matrix rate-limits get aggressive or when a debug session wants the
old shape.

State machine (see `_StreamReplyState`):

- **Placeholder gate**: no `room_send` until the agent has produced
  at least `STREAM_MIN_CHARS_BEFORE_PLACEHOLDER` characters. Prevents
  a "…" flash for turns the agent immediately silent-drops (which
  would then need a redact — extra event traffic + brief room
  pollution).
- **Edit debounce**: `STREAM_EDIT_DEBOUNCE_MS` + `STREAM_EDIT_MIN_DELTA_CHARS`
  must BOTH pass. Time alone lets a fast token stream ship a wall of
  1-char edits; delta alone lets a slow-generating agent ship 30
  edits per second the moment enough chars land. Both together
  keeps Matrix's per-room write budget happy.
- **Finalise with reply**: on `narra_reply(text=...)`, the accumulated
  streamed text is OVERWRITTEN by `text` via one last `m.replace`.
  This is the design's core safety property: any transient thinking
  we streamed is gone from the visible message by the time the turn
  ends. If no placeholder ever shipped (agent replied instantly),
  finalise falls back to `_send_matrix_reply` — retry-aware atomic
  send.
- **Finalise silent** (no `narra_reply`): redact the placeholder if
  one was sent; otherwise no-op. Room stays clean.

New HTTP surface in [[_matrix_send]]: `matrix_room_edit` (MSC2676
`m.replace` shape) and `matrix_room_redact` (Matrix's canonical
delete). Kept in the send module so future MCP tools can use them
too — they don't need trigger-instance state.

Behaviour vs Narra guide: OpenClaw's config sets `streaming: true`
under the assumption the runtime edits messages incrementally
(setup guide section 6b). NarraMessenger app already supports
message edits (Matrix native), so streamed edits render as one
message that updates in place. Cross-client compatibility: older
clients that ignore `m.replace` see BOTH the placeholder AND the
final edit as separate messages with a `* ` prefix on the edit —
mildly noisy but never broken.

Tests: `tests/narramessenger_module/test_matrix_streaming_reply.py`
(12 tests: placeholder gating, edit debounce, thinking ignored,
narra_reply capture / last-wins, finalise-with-reply edit path,
finalise-with-reply no-placeholder fallback, finalise-silent redact,
finalise-silent no-op, feature-flag off).

## 2026-07-03 (fix) — NarraMessenger "compound" multimodal, not inline m.image

Live test (agent_62cf67080ad4, 2026-07-03) showed the Phase-3 inline-media
path never fires for real NarraMessenger pictures — because NarraMessenger
does NOT send standard `m.image` events. A picture arrives as a **compound
message**: a plain `m.text` event whose custom
`content["ai.netmind.hint"]` = `{kind:"compound_trigger", compound_preview:
{text, media_url(mxc), mime_type, file_name}}`, plus a sibling
`msgtype:"ai.netmind.compound"` event (nio → RoomMessageUnknown) carrying
the raw bytes. Our code only read the `m.text` `body`, which is the *hidden*
`"[internal hint] process compound $…"` string (`ai.netmind.visibility:
hidden`) — so the agent got a cryptic reference, no image, and stayed
silent.

`_wrap_event` now detects `compound_trigger` and lifts the preview into a
`kind="m.room.message.compound"` raw dict: the REAL user text (not the
hidden body) + the mxc. `parse_event` turns that into a ParsedMessage whose
`content` is the user's text and whose `attachment_refs` points at the mxc —
so the **existing** `fetch_attachments` → `_download_mxc` →
`_persist_attachment` pipeline downloads it unchanged. The raw
`ai.netmind.compound` (RoomMessageUnknown) sibling is ignored — the preview
has everything, and NarraMessenger 403s our direct `/event` + `/messages`
reads, so the pushed /sync event's preview is our only handle.

An INFO log fires on each `compound_trigger` (media present + mime) so a
live run confirms the branch is reached. The old `m.room.message.media`
path is kept for any room that genuinely sends inline media.
Tests: `test_matrix_compound_ingest.py` (wire format captured verbatim from
the live event).

## 2026-07-03 (fix) — extract_output was reading the wrong raw_items shape

First live reply test: the agent DID call `narra_reply`, but the reply never
reached the room — logged "agent chose silent reply; nothing sent". Cause:
`extract_output` read `getattr(item, "details", …)`, but
`run_collector.collect_run` emits tool calls as **dicts**
`{"item": {"type":"tool_call_item","tool_name","arguments"}}` (the shape Lark's
extractor navigates). `getattr(dict, "details")` is always None → no match →
"" → dropped. The original send_message_to_user_directly version had the same
mismatch but was never exercised (prompt pointed at narra_send), so the bug
rode along until the reply path actually went through narra_reply. Now mirrors
Lark: `raw.get("item")` → check `type=="tool_call_item"` → `item.get("tool_name"/
"arguments")` (arguments may be a JSON string). The unit test now uses the real
shape, not a `.details` stand-in.

## 2026-07-03 — reply unified onto Matrix (extract_output reads `narra_reply`)

Sibling to the outbound-send unification (see [[_matrix_send]]). `extract_output`
now scrapes **`narra_reply`** (the agent's channel-reply marker, ``text`` arg),
NOT the generic ``send_message_to_user_directly`` — the shared channel prompt
(`channel_prompts.py`) reserves that generic tool for OWNER messages, so keying
the room reply on it was the bug behind "reply went out via Gateway `/chat/send`
while the room_send path idled and mislogged 'nothing sent'".

NarraMessenger stays **trigger-driven** on purpose (the agent calls `narra_reply`
as a marker; THIS trigger does the actual `room_send` in `_build_and_run_agent`
→ `_send_matrix_reply`). That's different from Lark/Slack/Telegram (where the
channel CLI tool sends and we only scrape) — owning delivery in the trigger is
the foundation for future progressive `m.replace` streaming (spec §7). Proactive
text (`narra_send`) and media (`narra_send_media`) are separate, immediate sends
via [[_matrix_send]].

## 2026-07-03 (Phase 3) — multimodal ingest (m.image / m.file / m.audio / m.video)

Promoted from the "Deferred to Phase 3" stub in the file docstring to a
working receive path. Diagnosed from a live report: a message carrying a
picture produced no agent reaction. Root cause was **not** a parse gap —
`_wrap_event` only recognised `RoomMessageText`, so every media event hit
the `else` branch and was dropped with a `skipping event type=…` debug
line **before** `parse_event` or any download could run. Three layers were
text-only; all three now handle media, mirroring
[[channel_trigger_base]]'s attachment contract that Lark / Slack /
Telegram / Discord already use.

Flow (one `RoomMessageMedia` — the shared base of Image/File/Audio/Video):

1. **`_wrap_event`** — `isinstance(event, RoomMessageMedia)` marshals the
   mxc URI (`event.url`), filename (`event.body`), and the `content.info`
   block (`mimetype` / `size`) from `event.source` into a
   `kind="m.room.message.media"` raw dict. mimetype is only a hint —
   `_persist_attachment` re-sniffs the real MIME from bytes.
2. **`parse_event`** — media kind → one `attachment_refs` entry
   (`mxc_url` / `original_name` / `mime_hint` / `size_hint`) + coarse
   `content_type` (msgtype first, MIME family fallback). `content=""`
   (the filename is the attachment's `original_name`, NOT message text),
   so a caption-less image still flows — the base guard passes because
   `attachment_refs` is non-empty. The raw dict is **copied** before
   adding refs so the base's dedup key (which still holds the original)
   is untouched.
3. **`fetch_attachments`** (override) — for each ref: parse
   `mxc://{server}/{media_id}`, size pre-check vs
   `backend_settings.max_upload_bytes`, `_download_mxc` → base
   `_persist_attachment` (workspace store + MIME sniff + audio STT →
   `Attachment`). Never raises; audits `EVENT_INGRESS_DROPPED_OVERSIZED`
   / `EVENT_ATTACHMENT_FETCH_FAILED` / `EVENT_ATTACHMENT_PERSISTED`.
   Structure is a near-copy of `DiscordTrigger.fetch_attachments`; the
   **only** channel-specific part is the download source.
4. **`_download_mxc`** — the single network seam (tests monkeypatch it).
   `GET {homeserver}/_matrix/client/v1/media/download/{server}/{media_id}`
   with `Authorization: Bearer {matrix_access_token}` (the **Matrix**
   token, not the Narra bearer). The legacy unauthenticated
   `/_matrix/media/r0/download` path is gone on `matrix.netmind.chat`
   (verified 2026-06-30). Streams with a `max_bytes` cap so a lying
   `size_hint` can't blow memory; over-cap mid-stream →
   `MatrixMediaError("oversized")`, which the caller audits as OVERSIZED
   (not FETCH_FAILED) via the `code` split — same idea as
   `DiscordSDKError.code`.

**Why the prompt "download location" is free (design note the owner
called out):** we deliberately route through `_persist_attachment`
instead of storing bytes ourselves. That helper generates the `att_`
file_id and writes under the WS-upload path, so
`Attachment.synthesize_marker` (ChatModule) later emits
`[User uploaded image: … path=<abs> … — use Read tool to view]` with a
path `resolve_attachment_path` can actually resolve. Storing to a
self-invented path/file_id would make the marker render `<unavailable>`
and the agent could not open the file. The test asserts this end-to-end
(marker path == `resolve_attachment_path` == on-disk bytes).

Sending images back (agent-generated) is still out of scope this version
(§8.2 of the spec). Encrypted media (`EncryptedEvent`) is still dropped —
rooms are plaintext by default; that remains the separate Phase 3+ item
noted below.

Tests: `tests/narramessenger_module/test_matrix_attachment_ingest.py`.

## 2026-07-02 (owner override) — SILENT_BYPASS_AUTHORIZE

Live E2E confirmed the diagnosis from the INFO-log hotfix: Narra's
`authorize-event` denies group events with `mentioned=False` by
policy (verified verbatim on `agent_62cf67080ad4` — every non-@
group message returned `allow=False` with no notice). The owner's
product intent is different: the agent has the right to hear a
group it's in, it just shouldn't reply unless addressed.

`_process_message` is reordered so classification happens FIRST,
then authorize-event runs only on paths where the agent will
actually reply / invoke tools / call the model. Specifically:

- `dm` / `group_mention` → authorize-event REQUIRED (unchanged;
  matches guide's intended domain).
- `group_silent` → authorize-event SKIPPED when the class-level
  constant `SILENT_BYPASS_AUTHORIZE = True`. The memory-only path
  proceeds regardless of Narra's decision.

`SILENT_BYPASS_AUTHORIZE` is a known-conflict-with-the-guide
constant, documented in the source and kept as a class field so it
can be flipped back to False in one line if NarraMessenger tightens
enforcement (in which case group non-@ collapses to Slack parity —
memory only when @-mentioned).

Renegotiation with the NarraMessenger team is in flight; the design
argument is that "listening to a room the agent has joined" is a
Matrix-native right, not a Narra-specific exception. When we hear
back, either the constant stays True (Narra agrees), or flips to
False and the design falls back to guide-strict.

## 2026-07-02 (post-Commit-7 hotfix) — diagnostic INFO + auto-disable broken creds

Two behavioural fixes surfaced during the first live E2E:

1. **Silent-path diagnostic logs promoted to INFO.** The
   `_process_message` decision points — client-missing drop, echo
   drop, authorize-event deny (with or without notice), classifier
   result — now log at INFO, and the silent-buffer lifecycle
   (`_enqueue_silent`, `_debounce_flush` fire/cancel, `_flush_silent`
   entry/exit, `_drain_all_silent_buffers`) is fully traced. Rationale:
   the first live test showed 0 `silent=True` runs on group non-@
   messages, and every silent-path failure mode was invisible at
   INFO. Now the log tells us exactly which branch a missing turn
   fell through.
2. **Broken credentials auto-disable on connect.** When
   `MatrixTrigger.connect` sees a `connection_mode='matrix'` row with
   an empty `matrix_access_token`, it now calls `disable_credential`
   BEFORE raising `ValueError`. Without this, the base's
   `_subscribe_loop` treated the ValueError as transient and retried
   every 120s forever, generating an ERROR + full stack trace on
   every retry. Pre-existing rows (pre-Matrix binds that never got a
   Matrix token) now flip `enabled=False` on first sight and the
   owner sees a single WARN telling them to re-bind, not a
   permanently noisy log stream.

## 2026-07-02 (Commit 7) — polling deleted; sole NarraMessenger trigger

`channel_name` reverts from `narramessenger_matrix` to `narramessenger`;
the `_matrix` suffix only existed to partition dedup / audit while the
polling `NarramessengerTrigger` coexisted. That trigger was deleted in
Commit 7, so the disambiguating suffix becomes clutter.

Practical consequence: any dedup rows written under
`channel_name='narramessenger_matrix'` become orphaned in
`channel_seen_messages`. Event-id uniqueness makes cross-partition
collisions impossible, so the orphans are just dead rows — cheap to
sweep via retention TTL, no data migration required.

`load_active_credentials()` no longer filters by `connection_mode`; it
calls the manager's `list_active()` and returns every enabled row.
Legacy Gateway rows without a `matrix_access_token` reach `connect()`,
raise `ValueError`, and get disabled by the base's watcher — the owner
is expected to re-run the bind flow to end up on Matrix.

The `_subscriber_key` still returns `matrix:<agent_id>`; provenance
documentation only, since no other trigger competes for the base's
subscriber map. Any consistent per-credential key works after
Commit 7 — no need to change it and lose the historical breadcrumb.

## 2026-07-02 (Commit 6) — auto-join invited rooms

The sync loop now walks `resp.rooms.invite` at the top of every batch
and calls `client.join(room_id)` for each invitee. Matches the setup
guide's OpenClaw config which sets `autoJoin: "always"`: the owner (or
any legitimate inviter) expects the agent to appear in the room without
a second confirmation step.

Failure handling: `client.join` errors are logged at WARNING and swallowed.
Rationale — the invite persists in `resp.rooms.invite` until the join
succeeds, so the next sync tick automatically retries. Propagating would
break the outer sync loop over a transient network blip and force a full
reconnect via the base's backoff path, which is a bigger hammer than
the failure warrants.

No dedicated audit event: auto-join is expected behaviour, not an
anomaly. If join throughput ever becomes ops-visible (e.g. mass
invites causing rate-limit trouble), promote to an audit line then.

## 2026-07-02 (Commit 5) — Narra authorize-event gate

Every Matrix event must clear
`POST {backend_base_url}/api/agent-runtime/matrix/authorize-event`
BEFORE we read history, write memory, invoke tools, call the model,
or send a Matrix reply. Applies to **both** silent and full paths —
silent still writes chat_history + observations, which the guide
explicitly lists as gated operations.

Implementation (`_authorize_event` + `_send_matrix_notice`):

- Uses the **Narra agent secret token** (`credential.bearer_token`), NOT
  the Matrix access token. These are different secrets with
  different scopes; the Matrix access token would return `401` from
  this endpoint.
- Fails **closed** on: non-2xx status, transport exception / timeout,
  invalid JSON body, `allow` field missing or not exactly `True`, or
  credentials missing `bearer_token` / `backend_base_url`. All → drop
  the event without further processing.
- 401 during pending bind is expected fail-closed behaviour (owner
  has not called `runtime-ready` yet). Logged at INFO, not WARNING,
  so it does not spam ERROR during the normal bind-in-progress
  window. It is NOT wired into `is_permanent_auth_failure` — that
  method only catches Matrix `M_*` codes, so a Narra 401 correctly
  will not disable the Matrix credential.
- On `allow != true` with `notice.send == true`, we forward exactly
  `notice.text` back to the same room as `m.notice`. This is the only
  side effect allowed for a denied event; no memory write, no tool
  call, no model call, no other reply. If `notice.send != true`, we
  drop silently.

Mention detection was split out of `_classify` into
`_is_mentioning_us` (Commit 5) so the gate can compute the
`mentioned` payload flag BEFORE classification runs, and `_classify`
reuses the value via a `mentioned=` kwarg — no double body scan.

## Why it exists

Phase 1 replacement for the polling long-poll on the NarraMessenger
message plane. NarraMessenger's backend runs a Matrix homeserver
(`matrix.netmind.chat`); this trigger is a `matrix-nio` client that
opens `/sync`, receives events causally, and posts replies back via
`room_send`. We host **zero** Matrix infrastructure — homeserver,
TLS, federation, storage, backups all belong to the NarraMessenger
team. The trigger runs in-process next to
[[narramessenger_trigger]] (polling), each subclass filters
credentials by `connection_mode` so both transports can coexist on
the same agent set during migration.

## Design decisions

- **Distinct `channel_name`**. `narramessenger_matrix` partitions the
  base's dedup store, audit log, and per-channel stats from the
  legacy polling trigger. Matrix `event_id` and NarraMessenger
  `invocation_id` live in different ID spaces and must not collide
  in one dedup bucket.
- **`working_source` stays `NARRAMESSENGER`**. Downstream of
  `parse_event` (context builder, module coordination, reply routing)
  treats Matrix and polling as the same product surface — the
  transport swap is invisible to Modules.
- **Non-`[e2e]` matrix-nio**. `matrix.netmind.chat` rooms are
  plaintext by default (verified empirically 2026-07-02 — new rooms
  have no `m.room.encryption` state event). Skipping libolm removes a
  native-C dependency and shaves ~30 MB from the runtime footprint.
  If NM ever flips a room to encrypted, `client.sync` will surface
  `EncryptedEvent` instances that we currently drop in `_wrap_event`
  — the failure mode is "agent stops seeing messages in that room",
  not "agent crashes". Phase 3+ concern.
- **Silent-batch buffering for group non-@ messages**. Group messages
  that don't mention the agent should still land in memory
  (chat_history, observations, entity descriptions) but MUST NOT run
  the agent LLM — that's the whole point of the memory-only path
  (see [[agent_runtime.py]] `silent=True` and
  [[channel_trigger_base]] `_build_and_run_agent_silent_batch`).
  Per-room debounce (5s idle) coalesces bursts into one silent-batch
  runtime call; the burst cap (20 msgs) forces flush on active
  channels so memory lag stays bounded.
- **Reply via `client.room_send`**. Unlike polling, there is NO 15-
  minute response-timeout ceiling — Matrix decouples message delivery
  from a synchronous request/response window. The agent can run for
  hours; when it finishes, one HTTP POST hands the reply to the
  homeserver, which fans out to every joined client (including the
  user's mobile app opened days later).
- **`extract_output` reads `notify_owner`**. Matches
  the discipline of Lark / Slack / Telegram: only content the agent
  explicitly passed to the reply tool ends up in the room. Never
  falls back to `result.output_text` — that's the agent's internal
  reasoning stream, and posting it as a reply would leak
  chain-of-thought into a user-visible channel.

## The three-way classification

`_classify(client, message, credential)` returns one of:

- `"dm"` — 2 members in the room. Full agent-loop path via
  `_process_message` → `_build_and_run_agent` → reply.
- `"group_mention"` — 3+ members AND agent explicitly mentioned. Same
  as DM. Mention detection covers (1) MSC3952 intentional mentions
  (`m.mentions.user_ids`), (2) raw MXID inline, and (3) `@displayname`
  inline — the three surfaces different clients emit.
- `"group_silent"` — 3+ members AND no mention, OR shape unknown (count 0:
  the one-time `joined_members` lookup failed / returned nobody — retried on
  the next message). Route to per-(agent,
  room) buffer, no agent LLM invocation. Flushed via debounce OR
  burst-cap OR reconnect-burst drain OR `stop()` drain.

## Cursor-save ordering (crash-safe restart)

The sync loop follows a strict ordering:

```
resp = await client.sync(since=X, timeout=30s)
for room in resp.rooms.join:
    apply member state events        # roster hint BEFORE classification (may be partial)
    for message event in timeline:
        yield event                    # base awaits _dedup_and_handle inline
if self._silent_buffer:
    await drain_all_silent_buffers    # backlog flush BEFORE cursor commit
update_since_token(next_batch)         # ONLY after all writes durable
```

If the process dies mid-batch, the cursor stays at `X`, the server
replays the same batch on next connect, and the base's event_id
dedup filters the already-handled messages. Persisting the cursor
before drain would lose the silent memory writes.

## Reply send retry policy

`_send_matrix_reply` handles the three real failure modes:

- `M_LIMIT_EXCEEDED` → honor `retry_after_ms`, retry. Not counted
  against `SEND_MAX_ATTEMPTS`.
- `M_UNKNOWN_TOKEN` / `M_MISSING_TOKEN` / `M_FORBIDDEN` → give up on
  the FIRST hit; the base's sync loop will trip
  `is_permanent_auth_failure` on its next `/sync` call and disable
  the credential. Do not burn 3 attempts against a dead token.
- Transient (network / 5xx / unknown status) → exponential backoff,
  cap `SEND_MAX_ATTEMPTS`.

Any failure that exhausts the retry budget writes an audit row of
type `transport_send_failed` (added to
`channel_audit_events.py` in the same commit) with `error_code`,
`attempts`, and a truncated `body_preview`. That's the only signal
owner-notification tooling can key on, so it MUST be written even
for permanent auth failures.

## What silent path DOES NOT send

The silent-batch runtime call fabricates an empty `PathExecutionResult`
(see [[agent_runtime.py]] silent-mode note), so `_build_and_run_agent`
is not on the path at all — no `_send_matrix_reply` call ever fires
for silent messages. The room never sees a stray "still thinking…"
or empty message from the agent while we're silently ingesting a
group thread.

## Related

- [[channel_trigger_base]] — base class; `_build_and_run_agent_silent_batch`
  is the shared silent-mode entry point.
- [[agent_runtime.py]] — `silent=True` runtime mode.
- [[chat_module.py]] — silent-batch write path in `persist_turn`.
- [[narramessenger_trigger]] — polling transport, coexists with this.
- [[_narramessenger_credential_manager]] — where matrix creds live
  (`connection_mode='matrix'` rows).

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
