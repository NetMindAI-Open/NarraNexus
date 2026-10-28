---
code_file: src/narranexus/platform/channel/channel_trigger_base.py
stub: false
last_verified: 2026-10-14
---

## 2026-10-14（预审后）— 看门狗收割死订阅者时同样释放 per-agent 基线

`_stop_subscriber` 与 watcher 的 dead-key 收割路径现在对称：都 `clear_agent_baseline(agent_id)`，不让一个已结束会话设的覆盖
活过它自己。锁：`test_credential_refresh.py::test_stop_subscriber_releases_*` / `test_reaping_a_dead_subscriber_releases_*`。

## 2026-10-14 — `_subscribe_loop` 每次（重）连先取 watcher 的最新凭据；historic 审计带 `age_min`

- **根因**：`_subscribe_loop(credential)` 把 spawn 时的凭据对象带过每一次重连，而 `_credential_watcher` 每轮
  （10s）都把 DB 快照写回 `_subscriber_creds[key]`，**包括** `BREAKER_VOLATILE_CREDENTIAL_FIELDS`（那个集合只
  影响熔断指纹，不影响缓存刷新）。结果 Matrix 每次重连都从订阅者启动时的 `matrix_since_token` 重放。现在每轮
  循环顶部 `credential = self._subscriber_creds.get(sub_key, credential)`（key 缺失则沿用）。缓存最多滞后一个轮询
  周期；要求精确游标的渠道（Matrix）仍在 `connect()` 里直读 DB。锁：
  `test_credential_refresh.py::test_reconnect_uses_the_watchers_fresh_credential_not_the_captured_one`。
- `_on_transport_connected` 默认仍推进**全局**基线（所有渠道包括 Matrix 都不覆写，行为不变）；Matrix 不读全局基线，改在 `connect()`
  设 per-agent 基线（见 [[channel_dedup_store]]、[[matrix_trigger.py]]）。
- `_stop_subscriber` 同时 `clear_agent_baseline`，不留残项。
- `ingress_dropped_historic` 审计 `details` 增加 `age_min`（分钟，1 位小数），事后能区分「回放」与「丢失」。
- **不做**：不对重放积压设年龄硬上限（铁律 #16）。

## 2026-09-11（PR #394 review 第五/六轮）— 静默批不过闸门、回执说真话；群聊拒绝提示按窗口节流

- **静默批不过熔断器闸门**（第五轮 N-1；理由第六轮 I-2 订正）：熔断器守的是**会花 agent 槽凭据的 turn**，
  `silent=True` 走 `SilentAct`（跳过 step_3，零 agent LLM 调用），一次都不起；它没有重试/补偿队列，
  PAUSED/PROBING/COOLING 下跳过都会让那段群聊**永久进不了记忆**。记忆/叙事侧 LLM 走 helper 槽，而默认单 key
  配置下 helper 槽**就是**熔断器可能扣住的那把凭据——暂停期间这些调用同样可能失败；helper 槽健康归 helper 侧管，
  不在本熔断器职责内（此前写的「不碰被扣住的凭据」不成立）。它从不传 `probe_token`，所以不认领也不结算探测。
  锁：`test_the_silent_batch_runs_whatever_the_breaker_holds`（三态都跑、行不变）/ `test_the_silent_batch_never_claims_the_probe`。
- **静默批返回结果**（N-2）：`_build_and_run_agent_silent_batch -> Optional[str]`，跑成返回 None，否则返回没跑成的
  稳定短码（第六轮 M-3）：模块常量 `SILENT_BATCH_EMPTY="empty_batch"` / `SILENT_BATCH_RUNTIME_RAISED="runtime_raised"` /
  `SILENT_BATCH_RUNTIME_ERROR="runtime_error"`；异常类名与 runtime error_type 只进日志，不进面向平台的回执。`managed_silent_ingest` 据此回执：
  跑成才写 `(silent group message ingested to memory - no reply)`，否则 `(silent group message not ingested - <reason>)`。
  原生 `group_silent` 调用方不读返回值。锁：`test_the_silent_batch_reports_a_pass_that_did_not_run`、
  `test_manyfold_im_ingress.py::test_base_managed_silent_ingest_drives_native_batch`。
- 被节流时日志写 `throttled (already claimed in this window: <window>)`（第六轮 M-4）：先占后发，挡住的那一刻
  第一条可能还在飞、甚至随后失败被回滚，所以不说「已发送」。
- **拒绝提示节流**：`_circuit_admission` 被拒后 `_claim_circuit_refusal_send(message, agent_id, admission.window)`（同步，
  check-and-set 之间无 `await`，并发消息不会双发）：群聊（`GROUP` / `TOPIC_GROUP`）按 `(channel_name, agent_id, chat_id)`
  每个熔断窗口只发第一条。窗口直接取 `TurnAdmission.window`（`admit_turn` 那**同一次读行**的 `cb_status|cooldown_until`，
  M-2：不再二次读行，文案与窗口同源）；修好→再坏、探测失败退避翻倍都是新窗口。私聊不节流；拒绝不带窗口则照发。
  **先占、发送失败回滚**（M-1）：claim 是 `RefusalClaim(key, window)` NamedTuple（第六轮 M-5；`window=None` 表示不节流、
  无可交还）。`_send_circuit_refusal` 返回 bool，False 时 `_unclaim_circuit_refusal_send(claim)` 交还窗口
  （只在该条仍是本窗口时删），下一条消息会再试；失败发送在飞期间到达的消息不会被补告知。节流的只是**发送**：
  `refusal` 仍作为 turn 输出返回，inbox 照记。记录在类级 `_circuit_refusal_windows`（进程内、OrderedDict、上限 4096 淘汰最旧），
  丢一条记录的代价是多提示一次。
- 发送钩子 `_send_circuit_refusal(credential, message, text) -> bool`：默认直接 `send_channel_reply`（抛错=失败，记日志吞掉）；
  [[matrix_trigger]] 覆写成 `_send_matrix_reply`（它的 `send_channel_reply` 是 no-op）。
- **`_run_agent_turn -> ChannelTurnOutput(text, refused)`**（N-3）：`_build_and_run_agent` 的 turn 本体拆出来，
  `_build_and_run_agent` 只返回其 `.text`。`refused=True` 表示拒绝文案的投递已归闸门所有；自己发送返回文本的渠道
  （Matrix atomic）调它并在 refused 时不再发送。第六轮 M-1 给这条契约加了门禁
  `test_only_registered_channels_consume_the_bare_turn_text`：`await self._build_and_run_agent(` 的调用方（基线
  2026-09-11：本类 `_handle_message` 与 [[lark_trigger]]，都只写 inbox）与 `async def _build_and_run_agent(` 覆写
  （本类 / lark / matrix）按文件计数登记，新增即红；覆写方必须出现 `_circuit_admission(` 或 `self._run_agent_turn(`。
  锁：`test_a_group_hears_the_refusal_once_per_breaker_window` / `test_a_failed_group_refusal_send_hands_the_window_back` /
  `test_a_private_chat_is_told_every_time` / `test_matrix_group_refusal_is_sent_once_per_window` / `test_matrix_atomic_*`。

## 2026-09-10（PR #394 review 第四轮 I-3）— channel turn 过熔断器闸门

此前 `_build_and_run_agent` 与静默批两处 `run_and_collect` 零闸门：一个凭据已死、被硬 PAUSED
的 agent，群里每来一条消息仍起一个真 turn、打一次坏 key、失败再播回房间。现在：
- `_circuit_admission(credential, message, agent_id) -> (TurnAdmission, refusal)`：在 prompt/anchor/
  extra_data 都建好、紧挨 `run_and_collect` 之前（现位于 `_run_agent_turn`）调 `circuit_breaker.admit_turn`（所有「不起 turn」
  分支——dedup、echo、非 @ 群消息、ingress guard——早已在 `_process_message` 上游返回）。被拒 →
  经 `_send_circuit_refusal`（默认 `send_channel_reply`，群聊按窗口节流，见 2026-09-11 节）把 `format_circuit_refusal(reason)` 发回会话，并作为 turn 输出返回（inbox
  照常记录）；不重试。放行 → `probe_token` 交给 `run_and_collect`（它结算探测），`finally` 里
  `_release_unsettled_probe` 兜底（token CAS，已结算即 no-op）。
- `format_circuit_refusal(reason)`：面向发消息的人（常常不是 owner），不给操作指引；paused →
  「暂停中、请联系 bot 所有者」，cooling/probing →「稍后再试」。子类可覆写。
- 静默批不过闸门、不认领探测（见 2026-09-11 节）。
- `_breaker_db()`：优先用 trigger 自己的 `_db`，否则进程 client。
覆写了 `_build_and_run_agent` 的子类（[[lark_trigger]]、[[matrix_trigger]] 流式路径）必须自己调
`_circuit_admission` / `_release_unsettled_probe`。锁：`tests/channel/test_channel_circuit_breaker_gate.py`。

## 2026-09-10 — `safe_error_text` 覆盖 `_subscribe_loop` 之外的三个异常审计出口（PR #388 review M6 / round-2 M4-M5）

**范围**：`EVENT_WORKER_ERROR`、`EVENT_ATTACHMENT_FETCH_FAILED`、`EVENT_INBOX_WRITE_FAILED` 三处 + 断连两分支。
`EVENT_MANAGED_INGRESS_PROCESSED` 的 `details.error`（`managed_after_run` 内，`:2295` 附近）**不走**它：那是入参，来自
completions 端点的 run 错误消息，同一文本已经 `format_error_reply` 回给用户，不含传输层凭据；走 `safe_error_text`
反而会把 32+ 位 run_id/message_id 打成 `<redacted>`。它的截断上限同样是 `AUDIT_ERROR_MAX_CHARS`（审计行），
不再是裸的 200 字面量。
**两个上限**：`DISABLE_REASON_MAX_CHARS=200`（凭据行 → 面板无折叠）与 `AUDIT_ERROR_MAX_CHARS=500`（审计行是排障
真相源，与同行的 `original_message`/`agent_response` 的 500 对齐）；`safe_error_text(exc, max_chars=...)`，五处审计
写入显式传 500，`disable_credential(reason=)` 仍用 200（永久分支两份文本分别算）。

除 `_subscribe_loop` 的两个断连分支外，`EVENT_WORKER_ERROR`、`EVENT_ATTACHMENT_FETCH_FAILED`（含其 warning 日志句）、
`EVENT_INBOX_WRITE_FAILED` 的 `details.error` 也统一走 `safe_error_text(e, AUDIT_ERROR_MAX_CHARS)`（脱敏 +
500 字符截断，几 MB 的异常文本不会整条进审计表）；全文件不再有裸 `"error": f"{type(e).__name__}: {e}"` 的审计写入。其余仅进日志句的
warning（DM 门、反应、managed ingress 等，不含传输层文本）未改。`logger.exception` 的 traceback 保持原样。
测试：`tests/channel/test_attachment_fetch_pipeline.py::test_fetch_attachments_raise_degrades_gracefully`
现在断言审计行不含 URL/token；`safe_error_text` 自身的打码/换行/两档上限单测在 `tests/channel/test_safe_error_text.py`。

## 2026-09-09 — `disable_credential(credential, reason="")` + `safe_error_text`（B-28，复审 I1/I3/I4）

`_subscribe_loop` 的**四个出口**——永久分支的 warning 与审计 `details.error`、瞬时分支的 exception 日志与审计
`details.error`——以及传给 `disable_credential` 的 `reason`，全部用同一个 `safe_error_text(e)`（复审 I1，
原名 safe_disable_reason）：异常类型 + 消息，URL（Telegram 的请求 URL 路径里就是 bot token）与 token 形状
（`<digits>:<base64>`、JWT、32+ 位不透明串）统一打码，再截断——覆盖 disabled_reason、**格式化的日志句**与审计
`details.error` 三处；截断上限自 2026-09-10 起分两档（见上方小节）：`disabled_reason` 用 `DISABLE_REASON_MAX_CHARS=200`，
日志句与审计 `details.error` 用 `AUDIT_ERROR_MAX_CHARS=500`。**但 `logger.exception` 渲染的 traceback 尾行仍是
原始 `str(exc)`**（复审 round-3 I1 实测），基类不能也不应改掉它（丢栈更难查），所以 `safe_error_text` 只是兜底网：
密钥必须在来源处抹掉——Telegram SDK 的 `TelegramSDKClient._redact` 在 aiohttp 异常文本变成 `TelegramSDKError` 之前
把 bot token 换成 `<token>`（信封 `error_detail`、`description`、`str()`、traceback 尾行一起干净）。审计 key 仍叫 `error`。
测试 `test_audit_and_disable_reason_never_carry_the_request_url_or_token`。**这是对所有子类的契约变更**：基类以关键字 `reason=` 调用，五个内置频道
（telegram/slack/discord/wechat/matrix）都已改签名并把 reason 持久化到 `disabled_reason`（各自 manager
`set_enabled(reason=)`），第三方插件频道覆写 `disable_credential` 时必须接受 `reason`。新静态方法
`log_disable_outcome(channel, agent_id, ok, reason)`：写成功 WARNING、写失败（store 返回 False）ERROR，
让"熔断没生效、还在重连"可观测。`matrix_trigger` 缺凭据自停用那条路径也带上了 reason。

## 2026-08-28（接线 review）— 清扫作用域与闸门次序

`_run_cleanup` 里那条 ingress 清扫补上 `channel=self.channel_name`，与它上面
两条对齐（理由见 [[channel_ingress_breaker_repository]]）。

`_process_message` 里「Last gate before anything expensive」这句改成陈述实情：
它是**本方法内**的最后一道门，但自己接管 `_process_message` 的渠道可能在委托
过来之前就花过钱——Matrix 的 dm / group_mention 两条路先付一次 Narra
`authorize-event` 往返。**记录而不是修**：把闸门上提会让 `super()` 对同一条
消息再判一次，滑窗记两遍，该渠道会在阈值一半就跳闸；要修得先给基类一条「本条
已判过」的通路，或让 Matrix 像 Lark 一样完全接管，都不是一行改动。代价是每条
被压制的消息多一次 HTTP，昂贵的那半仍然被挡住。

## 2026-08-26 — `is_agent_peer` seam

新增可覆写 hook，默认 False（多数渠道每个发送者都是人，猜错这个方向不改变
任何处理，只是少给模型一个提示）。[[matrix_trigger.py]] 覆写它。

**答案在这里产生一次**，因为只有 trigger 层知道各平台的身份约定；然后搭
[[channel_tag.py]] 走到上层，让消费方读同一个定义而不是各自再推一遍。
必须纯且便宜——每条消息都会调。

**契约是「实现只依赖 `message`」**，写在 docstring 里，并由守卫测试用
unbound 调用（`cls.is_agent_peer(None, msg)`）间接强制。

有人会问为什么不直接 `@staticmethod` 让语言来管——**考虑过，暂不做**：改
签名要动五个填充点和 Matrix 覆写，而且要先确认没有哪个渠道将来真的需要
`self`（若需要，该松的是这条约束本身而不是实现）。放到 seam 的消费方都落地
之后再一起清。

本类里两个 `ChannelTag` 构造点（单条消息 + 静默批）都填了。
`test_agent_peer_signal.py` 有一条守卫：**每个 ChannelTag 构造点的数量必须
等于填充数量**——漏填不会报错，只会静静地报「这是人」，正是
`build_trigger_extra_data` 当年那个缺陷类（四处手抄、新键只加了一处）。

## 2026-08-25 — `start()` 预热 ingress 熔断器；`_stop_subscriber` 释放会话

**① `start()` 里 `await self._ingress_guard.warm_start(channel_name)`。**
这是本页 2026-08-24 条目里那两个 health 计数**在重启之后是否可信**的全部
前提：两个观测面读的都是**内存**，重启后 `_sessions` 是空的，所以库里有
50 条 tier>0、10 条还在 24h 冷却，`/healthz` 照样报 0——发布一次看板就重新
变绿，而那些会话仍然是聋的。

预热**只加载仍在冷却的行**（`cooling_only`）。加载全部 tier>0 会把 I4 的
「重启后谎报 0」换成「随部署次数单调虚高」，因为那张表只增不删（retention
只扫 tier=0）。升级记忆不需要预热——`_load()` 在该会话再次说话时懒加载，
那正是它起作用的时刻。

**② `_stop_subscriber` 里 `forget_agent(agent_id)`。**
`prune_idle` 刻意保留带升级记忆的会话，所以解绑凭据后那些会话会驻留到进程
结束。持久行不删（re-bind 不该给惯犯发新预算）。
`test_ingress_guard_all_paths.py` 新增了一条守卫：**guard 的生命周期方法
必须真的有调用方**——`forget()` / `forget_agent()` 前后两次都是「写了
docstring 声称有人调、实际零调用」，这条测试把那类假承诺变成红灯。

## 2026-08-24 — ingress 熔断器接线：为什么是三个挂载点而不是一个

新增 [[ingress_guard.py]] 的调用面。守卫本体的设计理由在它自己的 mirror 里；
这里只记与本文件有关的两件事。

**一、没有单一 chokepoint，所以 seam 是方法不是位置。**
接线前的假设是「`_process_message` 就是那个咽喉」。实际情况：

- Slack / Telegram / Discord / WeChat：Telegram / WeChat 也 override 了，
  但都调 `super()._process_message` → 继承本类的闸门
- **Lark**：override 且**不调 `super()`**（它连 `_subscribe_loop` / `_worker`
  一起接管了）→ 必须在 [[lark_trigger.py]] 单独挂一份
- **Matrix**：调 `super()`，但 `group_silent` 分支**在此之前 return**，而那条
  路仍然跑记忆管线 → 那个分支要单独挂
- **托管模式**：整条原生接收路径都不走 → [[managed_channel_ingress.py]] 自挂

所以抽了 `_ingress_admitted(credential, message)` 作为**唯一的 seam**，四处
调它，再用 `test_ingress_guard_all_paths.py` 钉住「每个自己实现
`_process_message` 的类，要么调 seam，要么调 `super()`」。这条测试第一次跑
就抓出了 Telegram / WeChat / Matrix 三个此前没被数进来的 override —— 与
`build_trigger_extra_data` 当年那次是同一个缺陷类（N 份手抄），修法也同一个。

**二、闸门插在 unbound / echo / empty 之后。**
回声是 agent 自己发的，空消息是解析器没认出来的。把它们计进对端频率，等于
让 agent 自己把自己熔断掉。

**其余改动**：
- `is_agent_peer(message)` 新 override hook，默认 False。**一个定义喂三个
  消费方**：熔断阈值收紧、DM 兜底禁用（[[step_3_agent_loop.py]]）、DM prompt
  措辞（[[channel_prompts.py]]）。三处各问一次是它们漂移的开始。
- `_content_fingerprint` 的哈希搬去 `ingress_guard.content_fingerprint`，
  **门控留在本类**：`CONTENT_DEDUP_WINDOW_SECONDS` 管的是「平台是否用新 id
  重投」，与熔断器问的问题无关，不能一起解开。
- `health_snapshot()` / `_maybe_heartbeat()` 加
  `ingress_breaker_open_count` / `_cooling_count`。**报数不报 key**——
  session key 里有 chat_id 和 sender_id，`/healthz` 不是列举谁在跟谁说话的
  地方，身份留在 audit 表。
- `_run_cleanup` 加 `channel_ingress_breaker` 的保留期清扫（只扫 tier=0 的行）。
- `INGRESS_*` 全套类属性 tunable，沿用本文件「类属性而非构造参数，子类一行
  覆盖」的既有惯例。

## 2026-08-21 — record_turn 接线 chat_id + chat_type（PR-2 自动 reach 记录）

两处 `self._inbox_recorder.record_turn(...)`(`_process_message` 与 `managed_after_run`)各加 `chat_id=message.chat_id, chat_type=message.chat_type,`。`record_turn` 用它把「本 agent 在这个渠道/会话能触达发件人」自动写进 social graph——但**只在 `chat_type==PRIVATE`(1:1)时记**(群 `chat_id` 是房间,记成个人 reach 会把私信投进群;见 [[inbox_recorder.py]] 同日 Critical 修复)。共享路径覆盖 slack/telegram/discord/wechat/narramessenger。

**`managed_after_run` 的 `counterpart_name` 补 `sanitize_display_name`**(预审 Important):它是三个 record_turn 站点里唯一没过清洗的,而 reach 现在把该名**持久**写进 `entity_name`(每次社交搜索渲染回上下文)——不清洗就成了持久化的 prompt-injection seam(换行 + 伪 `SYSTEM:`)。另两条路本就 sanitize。守卫 `test_mock_channel_trigger_integration.py::test_managed_after_run_sanitizes_...`。

reach 路径上的 `"Unknown"` 哨兵(`sanitize_display_name` 空串兜底 + `_process_message` 的「名字为 Unknown 就 resolve」比较点)改用 `UNKNOWN_SENDER_NAME` 常量(增量审 Minor:生产者与比较点**同步**改,否则常量日后变值时「解析不出就 resolve」分支会静默失效)。非 reach 的展示默认值(`:1739` 等)按「不必一次扫完」留。

## 2026-08-17 — inbox 写入换成 InboxRecorder

`self._inbox_writer`（`ChannelInboxWriter`，写 MessageBus 的表）换成
`self._inbox_recorder`（`InboxRecorder`，写 inbox 自己的表）。两个调用点都改了。

动机不是整洁：旧写入器为了让面板找得到会话，**给 agent 建 `bus_channel_members` 成员行**，
而没有任何人推进它的 `last_read_at` ——于是每条 IM 消息都永久挂在 agent 的 bus 未读游标上
（prod 实测 1,364 条 / 90 个 agent）。见 [[inbox_recorder]]。

`owner_user_id` 是新增的必要信息（线程按用户列），由 `resolve_owner_for_agent` 走
`AgentRepository.resolve_owner` 这条共享缝解析，不在这里另开查询。

## 2026-08-17 — "从没执行过"不能写成 0.0（monotonic 从开机计数）

`_last_heartbeat_monotonic` / `_last_cleanup_monotonic` 的初值从 `0.0` 改成
`float("-inf")`。两道门都是 `time.monotonic() - mark >= interval`，而
`time.monotonic()` 在 Linux 上**从开机计数**——所以 `0.0` 表达的不是"从没执行
过"，而是"在开机那一刻执行过"。宿主机 uptime 小于间隔时，门**静默不开**：

- **heartbeat（600s）**：刚开机的宿主机头十分钟没有任何 L2 存活行——而那正是
  启动失败最可能发生的窗口，也正是事故教训 #4 要求这些心跳覆盖的东西。
- **cleanup（24h）**：`_run_cleanup` 自己的 docstring 写着 "once at startup +
  daily"，而新开的 EC2 实例两样都没拿到，第一次 retention sweep 要等宿主机
  uptime 满一天。

`-inf` 让"从没执行过"成为真正的哨兵，第一轮必然执行。**长 uptime 的机器上行为
不变**（`now - 0.0` 本来就远大于间隔），这次只修好刚开机那一档。两个 mark 都只
参与差值比较、从不进 payload，所以无穷不会泄进任何一行审计数据；heartbeat 里的
`uptime_seconds` 用的是 `_startup_time_ms`（wall clock），不受影响。

**别改成 seed 语义**（`_thinking_batcher.py` 那种 `if mark == 0.0: mark = now`）
——那表达的是"第一次调用不执行、从此刻开始计时"，与这里想要的正好相反。

线索来自反方向：`test_credential_breaker.py` 里同一个 `0.0` 写法让一条测试在所有
开发机上绿、在全新 CI runner 上红（索引空列表）。测试那处是实例，这两处是同一个
假设在生产侧。守卫见 `tests/channel/test_first_cycle_on_a_fresh_host.py`（把
`time.monotonic` 垫成刚开机，是这个差别唯一可观测的条件）。

## 2026-08-10 — processed 行合并 audit_details(review 后:seam 撤销)

`managed_after_run` 新增 `audit_details`(completions 端点才知道的
turn 事实:route/duration_ms),合并进 `managed_ingress_processed`
行(事件名改用 `EVENT_MANAGED_INGRESS_PROCESSED` 常量);本方法自己
的 `replied`/`error` 键**后写覆盖**,调用方伪造不了。初版加过
`managed_audit` seam 供 coordinator 借道写审计,review 指出两条
deny 路径在 trigger 不可用时触发、借道机制天然记不了——coordinator
改为直接持仓库写,seam 撤销。

## 2026-08-10 — managed_reply_kwargs seam(managed turn 的信封第二半)

新 seam:managed turn 不跑 context builder,#254 信封的
`channel_reply_kwargs` 由 ingress coordinator 向 trigger 索取
(trigger 懂自己渠道的寻址,coordinator 保持渠道无关)。基类缺省
`{}` —— step_3 兜底只用 channel_tag.room_id 投递,对房间寻址渠道
(matrix/telegram)天然正确;token 寻址渠道覆写(wechat 用
`reply_token`)。native 路径不受影响(builder 的 reply_kwargs 照旧)。

## 2026-08-10 — owner 转发 wrapper 注解放宽为 Optional[str]

`resolve_owner` 拆分 ""(不存在)/None(查询失败)后(PR #258),本文件的转发
wrapper 如实透传 None;全部消费方按 truthiness/`or agent_id` 兜底,行为不变,
只是签名与 docstring 不再谎称"永远返回 str"。


## 2026-08-07 (二次) — 两处小收口

- `PLATFORM_REPLY_TEXT_KEY` 的延迟 import 提到模块顶层。同一次改动里刚以「无循环依赖」
  为由把 step_3 的六处同模块延迟 import 上提，这里却留了一处，下一个读者会以为这里有坑。
  确认无循环：`channel/__init__.py` 先 import `message_source_handler`、后 import
  `channel_trigger_base`；本文件顶部关于延迟 import 的 NOTE 讲的是 `agent_runtime.client`
  那个反向依赖，与本模块无关。
- `build_trigger_extra_data` 的 docstring 原来举例说 `**extra` 承载 NarraMessenger 的
  `rtc_voice`，但代码里 `rtc_voice` 是在调用**之后**单独赋上去的（它依赖后面才算出的
  `turn_profile`）。这份 md 当时写对了、docstring 没跟上。**docstring 是这个座位的契约
  说明**，举的例子和代码不一致比不举例更糟，已改为写明 rtc_voice 刻意不走这条路及原因。

## 2026-08-07 — `build_trigger_extra_data`：四个手搓构造点收成一个（review 收口）

昨天的信封只加在 `_build_and_run_agent` 一处，而 `trigger_extra_data` 这个 dict
在仓库里有**四个**构造点：本文件两处（单条消息 / silent batch）、
`LarkTrigger._build_and_run_agent`（完整覆盖基类方法）、
`MatrixTrigger._build_and_run_agent_streaming`（`STREAMING_ENABLED` 是默认路径）。
于是 Lark p2p、NarraMessenger 私聊、silent batch 三条链路的 `channel_room_type`
恒为空，`step_3` 把它们全判成群聊，**1:1 兜底在这些渠道上是死代码**——和「信封挂错
对象」是同一个缺陷类，第二和第三个副本。

新增 `build_trigger_extra_data(channel_tag, retrieval_anchor, trigger_id,
builder=None, attachments=None, **extra)`：公共键 + 信封 + attachments 一处组装，
`**extra` 收各路径差异项（Lark 的 `source_message_id`、Matrix 的 `rtc_voice`、
batch 的 `batch_messages`），空值丢弃。四个构造点全部改为调它。**这才是让「下一个
信封键」不可能再漏渠道的做法**——不是在四个地方各补一行。

`builder=None` 时不产出信封（silent batch 没有 context builder），降级为「不是私聊」
= 不兜底，正确：那种 run 本就 `silent=True`、不回答任何人。`turn_envelope()` 抛错
也只降级、不打死入站消息。

测试 `tests/channel/test_trigger_envelope_every_channel.py` 除了钉行为，还有一条
**grep 级守卫**：全仓扫 `"channel_tag": channel_tag.to_dict()` 字面量，除本文件外
出现即失败。行为测试抓不到「新渠道又手搓一份」，只有这种守卫能。

## 2026-08-07 — `platform_written_reply` / `resolve_agent_response`：平台代写的回复不再被记成沉默

`step_3` 的私聊兜底投递成功后会合成一帧带 `PLATFORM_REPLY_TEXT_KEY` 的 tool_call，
`run_collector` 把它折回 `result.raw_items`。但 trigger 侧的 `extract_output` 是**另一条
独立抽取路径**，各渠道只认自己那套参数形状：微信读 `arguments["text"]` → 空；
Lark 要求 `command` 里含 `+messages-send` → 空；slack/telegram/discord 同构。全部落到
`CHANNEL_SILENT_SENTINEL`。

而这个返回值就是 `ChannelInboxWriter` 写进 `bus_messages` 的 agent_response。所以
**真发出去了、记录成「(stayed silent)」**。对微信不只是记录难看：
`WeChatContextBuilder.get_conversation_history` 明确从 `bus_messages` 读回最近轮次，
**下一轮的 Conversation History 里 bot 那句就是「(stayed silent)」**——和 handler 层
修掉的「占位符污染上下文」是同一个故障，只是发生在另一层。

收口方式与 handler 层同构：新增 `platform_written_reply(result)` 与
`resolve_agent_response(result, message, credential)`，后者**先认平台回复、再落到
渠道的 `extract_output`**；两处基类调用点与 Lark 的两处调用点都改走它。
**六个渠道的 `extract_output` 一个都没动**——逐个渠道教它认这个键，正是本次改动在
handler 层已经拒绝过的做法。

## 2026-08-06 (二次) — 轮次信封带上 room_type 与渠道投递参数

`_build_and_run_agent` 组 `extra_data` 时多一行
`extra_data.update(builder.turn_envelope())`，带进两个通用键：
`channel_room_type`、`channel_reply_kwargs`（见
`channel_context_builder_base.py.md` 同日条目）。

消费者是 `step_3`：判断这轮是不是 1:1 私聊，以及在模型一个表达工具都没调的
情况下，用什么参数替 agent 把回复投递出去。**键刻意是通用的**——编排层不该
知道任何渠道的细节（铁律 #3），微信的 `context_token` 对它只是
`channel_reply_kwargs` 里的一个不透明键值。

## 2026-08-06 — 空内容丢弃补审计

`_process_message` 的空内容守卫（无正文且无 attachment_refs）从裸
`return` 改为先写 `EVENT_INGRESS_DROPPED_EMPTY` 再返回。动机：提取器
不认识的 payload 形状（8/6 Lark 无包裹 post 事故）在这里与真空消息
不可区分地消失，「bot 为什么没回」从审计表答不出来。丢弃语义不变，
只是留痕；caption-less 文件上传（有 refs 无正文）不受影响，仍然放行。

## 2026-08-04 — 起不来的凭据：预检门 + 快速死亡熔断器

prod 实锤（8/1-8/3）：某 agent 的 Lark App Secret 被清空后，订阅器每次启动
即静默 `return`（不抛异常，`is_permanent_auth_failure` 永远看不见），
watcher 无条件重启同 key → ~28s 死亡重生死循环，4h 刷 1498 次 WARNING。

**两道闸，先便宜后通用**：

1. `should_start_subscriber(cred)` 覆写钩子（默认 True，不做 I/O）——能只
   看凭据就断定「连都连不上」的，一次都不启动。Lark 覆写为
   `receive_enabled()`（secret 空即 False），本次事故因此是 0 重启 0 ERROR。
   拒绝走 `_note_unstartable`：**按指纹去重**，一个凭据状态只报一次
   （WARNING + `subscriber_unstartable` 审计行），换绑后重报。
2. 熔断器——一切**事前不可知**的兜底。每 key 数连续快速死亡（存活 <
   `BREAKER_FAST_DEATH_SECONDS`=60s），连续 `BREAKER_FAST_DEATH_THRESHOLD`
   （3）次触发，按 `BREAKER_BACKOFF_SCHEDULE_SECONDS`（5min→30min→2h，
   末档循环）隔离重探。与 `is_permanent_auth_failure` 互补而非替代：那条
   路需要可识别的异常类，熔断器认的是「反复秒死」这个形状。

**指纹是本设计最锋利的一处，两次都能把熔断器废掉**（review 抓出，8/4 修）：

- **取样时机**：指纹必须取自「这次启动实际用的凭据」
  （`_subscriber_start_fingerprint`，启动时落），不能取 `_subscriber_creds`
  ——后者比本轮 poll 慢一拍，两次 poll 之间任何一次写库都会被读成换绑，
  熔断刚跳闸就在同一轮被清除。
- **字段范围**：必须排掉订阅器自己回写的簿记字段
  （`BREAKER_VOLATILE_CREDENTIAL_FIELDS`，子类扩展）。Matrix 的
  `matrix_since_token` 每次 sync 都写、Lark 连上后写 `bot_open_id`/
  `bot_name`——不排掉就是「自己的流量把自己的熔断器清了」。
- 兜底：指纹触发的解除走 `_breaker_release`（**保留** `_breaker_trips`
  升档记忆），所以哪天漏排一个高频字段，代价是每档 3 次重启并继续升档，
  而不是无限风暴。watcher 会在订阅器**仍存活**且越过 60s 快死窗口时立即
  `_breaker_forget` 全清；判定时还必须检查 task 尚未结束，防止同轮 reap 的
  `await` 期间产生竞态。不能要求健康订阅器先死亡才结算，否则一次成功的退避
  恢复会永久抬高下次故障的隔离档位。
- 默认实现仍以字段 `repr` 组成指纹，因此 auth-relevant 字段值必须跨 DB load
  有稳定 repr；地址型对象字段必须列入 volatile，或由子类覆写成稳定编码。
  顶层凭据对象不可 introspect 时使用常量指纹，fail-safe 保持隔离。

其余不变量：健康存活（≥60s）清零 streak + trips（网络抖动不误熔断）；主动
`_stop_subscriber` 先 pop 掉启动标记（不计死）；凭据整行消失
`_breaker_purge_stale` 清全部五个 dict；`stop()` 一并清空（生命周期对称）。
日志/审计：trip=ERROR 一条 + `subscriber_breaker_tripped`；每次解除（含
退避到期重探）=INFO 一条 + `subscriber_breaker_cleared` 带 `reason`，
DB 轨迹不留空白（教训 #5）。心跳与 `/healthz` 另外常驻上报隔离态
（教训 #4，见 [[channel_health_server.py]]）。
Pin: tests/channel/test_credential_breaker.py。

## 2026-08-04 — 第三个 managed 缝:`managed_silent_ingest`(review)

静默摄取从协调器收回本类:批量调用的形状(credential/sender 表/
attachments_by_index)是 trigger 的私有编排知识,协调器伸手进私有方法
= 缝要消灭的耦合;渠道也因此获得覆写点(某渠道可选择不做静默)。
## 2026-08-03(补) — `_persist_attachment` 尾段抽到共享函数

store+STT+Attachment 构造迁至
`attachment_storage.persist_attachment_bytes`(managed ingress 转换器
共用);trigger 保留 owner 解析 + MIME sniff。行为不变。

## 2026-08-03 — managed-ingress 缝(start() 之外复用业务钩子)

新增 `_managed_bind` / `_credential_for_agent` / `managed_before_run`
/ `managed_after_run`:Manyfold 托管模式下平台持有连接与清洗,
openai_compat 经 managed_channel_ingress 构造 trigger(不 start)并在
run 前后调这两个缝。默认 before=放行;after=错误兜底(run 失败且无回复
→ `_send_error_fallback` + `format_error_reply(RunError)`)+ 原生
inbox write(无回复写 CHANNEL_SILENT_SENTINEL,与原生 extract_output
语义一致)+ `managed_ingress_processed` 审计行(教训 #5)。
`_managed_bind` 等价于 start() 里业务钩子所需的最小状态(_db +
audit repo),幂等。覆写:wechat(认主)、matrix(authorize)。

## 2026-07-31 — _resolve_agent_owner 委托 AgentRepository.resolve_owner

实现收敛到 repository seam;2026-08-10 起契约随 resolve_owner 拆分:miss 回 '',lookup 失败回 None(见顶部条目)——属主语义
从此一个家（PR #219 review 收敛三份拷贝）。

## 2026-07-22 — _sniff_mime delegates to the shared utils helper

``_sniff_mime`` now calls [[mime_sniff]] (one tiering for IM downloads, WS
uploads and team-chat uploads). Two deltas for THIS path: the platform
``hint`` dropped from tier 2 to last resort (extension guess now outranks
it; content sniff still outranks both), and the audio/video container
override applies here too — an audio-only WebM/Ogg/MP4 whose platform hint
says ``audio/`` no longer classifies as ``video/``.

## 2026-07-10 — "ack early" moved into the per-turn input (salience)

The early-feedback directive used to live in each module's `contribute_instructions`
(system prompt) where models deprioritized it. It now rides in the **per-turn
input** instead: `_build_and_run_agent` prepends `_early_feedback_prefix(message)`
to `tagged_prompt` (right after the channel tag), so the "ACK FIRST" line sits
inline with THIS message — higher salience. `_early_feedback_prefix` uses the
new `react_tool_ref` class attr (bare name / Lark's `mcp__…` / None for WeChat →
message-only ack) + the real room/message ids, via `render_early_feedback`.
Still a SHOULD (prompt, not a hard guarantee). Lark's `_build_and_run_agent`
override injects the same prefix.

## 2026-07-10 — surface source_message_id (agent-driven feedback enabler)

`_build_and_run_agent` adds `source_message_id` (the inbound platform message
id) to `trigger_extra_data`. It merges into `ctx_data.extra_data`, so a channel
module's contribute_instructions can tell the agent which message to react to /
reply in-thread (the agent-facing `react_to_user_message` tool). Kept here, not
in ChannelTag, so it stays ephemeral (not persisted into chat-history tags).

## 2026-07-08 — `pre_start(db)` hook added for the consolidated supervisor

New optional lifecycle hook `pre_start(db)` (default no-op), called by the
consolidated supervisor (`module/run_channel_triggers.py`) BEFORE `start(db)`.
Subclasses override it to run their own idempotent one-off migration inside the
channel instead of in the shared entrypoint (rule #4). First user:
`LarkTrigger.pre_start` carries the legacy `auth_status` migration that used to
live in the now-deleted `run_lark_trigger` entrypoint. The design decision
"6 abstract methods + 1 optional hook + 2 PUSH stubs" below now reads "…+ 2
optional hooks…" (`fetch_attachments` and `pre_start`). Consolidation relies on
`start()` already being non-blocking + all state being per-instance, so N
triggers coexist in one event loop.

## 2026-07-07 — error-fallback: surface run failures INTO the channel

Problem: IM delivery is "agent calls its own reply tool during the run; the
trigger only scrapes the sent text for the inbox." So if a run FAILED before
the agent reached its reply tool, nothing was sent to the channel — the user
saw silence, indistinguishable from the agent choosing not to answer. Chat had
a helper_llm fallback; IM was excluded. (slack/discord/telegram/wechat wrote
the error to the inbox ONLY; lark already sent it; matrix uses streaming
markers.)

Fix — three pieces, all in `_build_and_run_agent`:
1. New overridable hook `send_channel_reply(credential, message, text)` —
   default no-op; each IM subclass implements it with the per-subscriber SDK
   client it already holds, addressing via `message.chat_id` / `sender_id` /
   `raw`. The runtime CANNOT do this itself (it has no channel client — those
   live on the trigger), which is why the fallback lives at the trigger layer.
2. `_send_error_fallback(...)` sends the error via the hook UNLESS the agent
   already replied this turn (`already_replied` → don't double-message),
   best-effort (a send failure is logged, never masks the original error).
3. `_build_and_run_agent` now: wraps `run_and_collect` in try/except (a hard
   raise, not just a yielded ERROR, still notifies — no silent crash); on
   `result.is_error` computes `already_replied` from `extract_output` vs
   `CHANNEL_SILENT_SENTINEL` and fires the fallback.

**Key safety property**: the fallback fires ONLY on `is_error` (or a raise). A
run that stays silent by CHOICE never sets `is_error`, so intended silence
(group non-@, nothing to add — see `_build_and_run_agent_silent_batch`) is
never disturbed. This deliberately does NOT recover the "agent wrote a reply
but forgot to call the send tool" (`no_reply`) case for IM — too ambiguous vs
intended silence; only errors are surfaced.

`CHANNEL_SILENT_SENTINEL = "(stayed silent)"` is now a shared module constant
(was hard-coded identically in 5 channels) so the base can tell "agent stayed
silent" from "agent replied" when gating the fallback. Lark's bespoke error
send was consolidated onto the same hook.

## 2026-07-02 — `_build_and_run_agent_silent_batch` for group non-@ ingestion

New instance method on the base: takes a non-empty list of
`ParsedMessage` (same chat_id, chronological), merges into one
`input_content` line-per-message with `[ts] Display: body`, then calls
`get_agent_runtime_client().run_and_collect(..., silent=True,
trigger_extra_data={"batch_messages": [...]})`. Per-message metadata
(event_id / timestamp / sender_id / sender_name / attachments) rides
in `batch_messages`; ChatModule's silent-batch write path (see
[[chat_module.py]]) reads it and appends N user rows to
`instance_json_format_memory` with NO assistant row.

Why here (channel-agnostic): all IM triggers have the same "group
message that didn't @ us" shape — Slack currently drops these at the
event boundary, Lark runs the full agent on every message, both
suboptimal. Landing the silent-batch shape on the base means each
trigger only needs a classification step (dm / group_mention /
group_silent) + a debounced flush; the batch runtime plumbing is
shared. Matrix (Commit 4b) is the first consumer.

The method is fire-and-forget for output: silent runs produce no
user-facing text, so no return value; failures inside the runtime
call are logged and swallowed to keep the sync loop / debounce timer
advancing (a dropped batch is recoverable via reconnect + since_token
replay, a crashed trigger is not).

## 2026-07-03 — CONTENT_DEDUP_WINDOW_SECONDS + _content_fingerprint (X1)

New opt-in class attr (default 0) wires ChannelDedupStore's
content-fingerprint layer; `_content_fingerprint` hashes
(chat_id|sender_id|content) → sha256[:32]. Policy stays in the subclass
(NarramessengerTrigger sets 20 min to cover the platform's 15-min
re-dispatch deadline); base computes the fingerprint so every channel
shares one identity definition. Chosen over shrinking
PROCESS_MESSAGE_TIMEOUT below the platform deadline, which would cut slow
LLM turns short (铁律 #14).

## 2026-07-03 — unparsed raw events now audited (`_on_unparsed`)

`parse_event(raw) -> None` (stickers/images/voice on text-only channels)
used to hit a bare `continue` — no log, no audit row, unanswerable "why
didn't the bot reply?" tickets (lessons #3/#5; 2026-07-03 wechat incident
burned an hour proving a message was never parseable). The subscriber loop
now calls `_on_unparsed`, which writes `ingress_dropped_unparsed` with the
raw item's KEYS only (never payloads — media bytes / text stay out of the
audit table).

> Concrete subclasses today: ``LarkTrigger``, ``SlackTrigger``,
> ``TelegramTrigger``, ``DiscordTrigger``. (The file docstring's old
> "Lark is NOT a subclass" line was stale; corrected when Discord landed
> — all four are subclasses now.)

## Why it exists

Phase 1's centerpiece. Direct extraction of the channel-agnostic 80%
of ``LarkTrigger`` into a base class so Slack (Phase 3) and Telegram
(Phase 4) ship without re-implementing dedup, Worker Pool, audit log,
inbox writer, or credential watcher. Lark itself stays put in Phase 1
— Phase 2 will refactor it onto this base.

This is the locus of the architecture's "Pattern C: shared subscriber"
principle (see ``.mindflow/project/references/architecture.md``).

## Design decisions

- **6 abstract methods + 1 optional hook + 2 PUSH stubs.** Subclasses
  implement ``connect``, ``parse_event``, ``is_echo``,
  ``resolve_sender_name``, ``create_context_builder``,
  ``load_active_credentials``. The optional ``fetch_attachments`` hook
  (added in Phase 1a, default returns ``[]``) lets channels with media
  ingestion override without forcing text-only channels to do anything.
  PUSH-mode stubs (``handle_webhook``, ``verify_webhook``) raise
  ``NotImplementedError`` until Phase 6.
- **``_persist_attachment`` helper.** Lives in the base because the
  download → MIME sniff → on-disk store → optional STT path is fully
  channel-agnostic. Each channel subclass downloads bytes from its own
  SDK then hands them to this helper. Workspace path resolution mirrors
  WS upload exactly (``_resolve_agent_owner(agent_id) or agent_id``)
  so the agent's Read tool finds the file at the same path the
  attachment was written to.
- **Attachment list flows via ``trigger_extra_data["attachments"]``.**
  Mirrors ``backend/routes/websocket.py:644-648`` so ChatModule's
  ``gather`` (which reads
  ``ctx_data.extra_data["attachments"]``) treats IM-uploaded and
  WS-uploaded files identically. The base only sets the key when the
  list is non-empty — keeps text-only audits noise-free.

- **Caption-less file uploads MUST flow** (Phase 1b regression-fix).
  The empty-content guard in ``_process_message``:
  ``if not message.content.strip(): return`` was originally written
  in Phase 1a when ParsedMessage was text-only — an empty content
  was a clear no-op. Phase 1b made files first-class, but the guard
  wasn't updated. Real-world failure mode: user drag-drops a PDF
  into Slack DM without typing anything → ``text=""`` +
  ``files=[...]``. parse_event correctly extracted ``attachment_refs``
  into ``raw``, but the base guard cut the message off BEFORE
  ``fetch_attachments`` could ever run, with NO audit row at all
  (the audit trail just stopped at ``debounce_merged``). The guard
  now keeps the early-return only when BOTH ``content`` is empty
  AND ``raw["attachment_refs"]`` is empty. Pin tested by
  ``tests/channel/test_attachment_fetch_pipeline.py::test_caption_less_file_upload_still_processed``.
- **Lazy AgentRuntime import.** Eager top-level import causes a
  circular load: ``channel/__init__.py`` re-exports
  ``ChannelTriggerBase`` for ergonomic use, but
  ``module/__init__.py`` (loading LarkModule) reaches the channel
  package first, so importing AgentRuntime here would re-enter the
  partially-initialised module package. Lazy-loading inside
  ``_build_and_run_agent`` breaks the cycle without forcing callers
  to import from the longer ``channel.channel_trigger_base`` path.
- **Tunable class attributes, not constructor args.** ``MIN_WORKERS``,
  ``MAX_WORKERS``, ``PROCESS_MESSAGE_TIMEOUT_SECONDS`` etc. are class
  attributes so subclasses can override them in 1 line; constructor
  args reserved for instance state (``base_workers``,
  ``history_config``).
- **``_subscriber_key`` override hook.** Defaults to
  ``credential.app_id`` (Lark today). Slack workspace install can
  serve multiple agents per ``team_id``; that channel will override
  to a compound key without changing the base.
- **Owner resolution via ``agents.created_by``.** AgentRuntime needs
  the agent's OWNER user_id to map provider quotas, NEVER the IM
  sender_id. This bug bit Lark previously and is fixed once here.
- **Per-message timeout, not just stream timeout.** The 30-min
  ``PROCESS_MESSAGE_TIMEOUT_SECONDS`` cap exists because
  ``collect_run`` only times out on stream silence, not total
  wall-clock — without this a stuck LLM call could occupy a worker
  forever.
- **``_prune_dead_workers`` between sizing decisions.** A worker that
  silently dies (cancellation leak, async-for oddity) would otherwise
  keep ``_adjust_workers`` from spawning a replacement, leaving the
  queue to grow unbounded. Lark's H-4 fix preserved here.
- **Credential cache is refreshed every poll, not just on subscriber
  start.** The credential is a DB snapshot whose ``permission_state`` /
  ``auth_status`` change mid-session — most importantly when the owner
  completes the three-click user authorization, which is what flips
  ``LarkCredential.user_oauth_ok()`` and lets ``resolve_sender_name``
  read names via the user token. The transport (``connect``) captures
  its credential once and the long-lived stream never re-reads it, so
  ``_credential_watcher`` overwrites ``_subscriber_creds[key]`` with the
  fresh DB snapshot on every poll, and ``_worker`` re-resolves the
  credential from that cache at dequeue time. Net effect: a mid-session
  auth completion (or any credential change) takes effect on the next
  message without restarting the subscriber or dropping the connection.
  Found on EC2: every Lark sender stayed "Unknown" because the running
  subscriber was started before the owner finished authorizing.

## Upstream / downstream

- **Upstream**: subclasses (Phase 2 will rebase ``LarkTrigger``;
  Phase 3 adds ``SlackTrigger``; Phase 4 adds ``TelegramTrigger``).
- **Downstream**:
  - ``ChannelDedupStore`` (3-layer dedup)
  - ``ChannelDebounceMerger`` (optional)
  - ``ChannelInboxWriter`` (5-row bundle)
  - ``ChannelTriggerAuditRepository`` (audit log)
  - ``ChannelSeenMessageRepository`` (durable dedup, owned by the
    dedup store)
  - ``AgentRuntime`` + ``collect_run`` (lazy import)
  - ``ChannelTag`` (prompt injection)

## Gotchas

- ``working_source`` defaults to ``WorkingSource.CHAT`` because every
  enum value must exist before the subclass picks one — Phase 1
  doesn't add ``WorkingSource.SLACK`` etc. Subclasses MUST set this
  to a meaningful value before ``start()``.
- ``handle_webhook`` raises ``NotImplementedError`` with a Phase-6
  reference — do NOT remove the references; webhooks need both
  HTTP routing AND a per-channel parser, neither of which is built.
- ``stop()`` flushes any debounce-buffered messages then drops them
  with a log line. We don't try to enqueue them on shutdown because
  the credential context (set per-flush in
  ``_enqueue_or_debounce``'s closure) isn't reachable from
  ``flush_all``. If a future channel really needs strict shutdown
  drain semantics, encode the credential into ParsedMessage.raw and
  reconstruct in ``_enqueue_debounced``.
