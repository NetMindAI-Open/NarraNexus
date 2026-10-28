---
code_file: src/narranexus/platform/utils/db/schema_registry.py
last_verified: 2026-10-08
stub: false
---

## 2026-10-08（PR #410 review）

`instance_browser_approvals` 不再注册（审批子系统删除）。不写 DROP TABLE（铁律 #6）：该表只在跑过此
分支的本地库里存在、为空，留着无害。

## 2026-09-23：浏览器登录通知与授权语义

新增 instance_browser_login_requests：跨 MCP/API 进程的人工登录通知，独立于特权能力
授权表；记录 request/agent/session/turn/thread、reason、state、连接归属及时间。
SQLite/MySQL 列齐全，仅增表，不缩窄已有字段。正常完成、取消或会话关闭后清理，
不保存密码、页面正文或截图。实际控制连接显式交还才完成；断线恢复待处理。
对应 tables.json 快照已更新。

浏览器表不承担普通 HTTP(S) 站点访问授权。turn/thread grants 和 decision receipts
服务于独立的特权能力，实际保存在策略 JSON，
以便另一个进程立即读取；是否适用于某次调用仍由可信运行时作用域匹配决定。
授权通知的消费先写入策略凭据再删除待办；没有声称存在未实现的按年龄清扫。

## 2026-09-10（PR #394 review 第四轮 I-1）— `probe_claimed_at` 换成 `probe_run_id`

`instance_agent_circuit_breaker` 的新列改为 `probe_run_id`（TEXT / VARCHAR(128)，与
`events.event_id` 同宽），additive、nullable、无回填；`probe_claimed_at` 从注册表删除（同一 PR
引入，未到任何已发布环境；本地已跑过迁移的库留下的空列无害，`auto_migrate` 不删列）。golden
`tests/snapshots/golden/tables.json` 已用 `NX_UPDATE_SNAPSHOTS=1` 重生成。

## 2026-09-10（PR #394 review I1）— `instance_agent_circuit_breaker` 加 `probe_claimed_at`

additive、nullable、无回填：半开探测认领时刻（`try_claim_probe` 写，所有离开 PROBING 的写
与 `probe_token` 一起清 NULL）。[[circuit_breaker]] `_claimant_may_be_live` 用它区分「认领
之后才开始的 run（可能是认领者）」与「更早就在跑的无关长 run」。golden
`tests/snapshots/golden/tables.json` 同步重生成（`probe_token` 那次漏了，正是 CI
`test_schema_registry_is_unchanged` 变红的原因）。

## 2026-09-09（review M6）— `bus_messages` 加索引 `idx_bus_msg_sender_time`

`(channel_id, from_agent, created_at)`，服务 [[local_bus]] `_resolve_part_group` 的「发件方最近
一块」与组内行查询。additive。

## 2026-09-09 — `bus_messages` 加三列 `part_index / part_count / part_group`（additive，可空）

长消息分片（[[multipart]]）。不改 `content` 的 TEXT 类型：`auto_migrate` 不做列加宽，
而且真正的截断点是发件方模型的输出预算而不是 64 KiB；单行上限由发送工具按字节明确拒绝
（`MAX_BUS_MESSAGE_BYTES`），超了就走分片，永不截断。

## 2026-09-09 — `bus_delivery_receipts` 表（26c，additive）

(message_id, to_agent) 复合主键；status / reason(已脱敏) / attempts / content_key +
一个索引（重发/掉包判定 `channel_id, to_agent, content_key`；曾有的发件方视图索引随
无调用方的 `for_sender` 一起删除，review M2）。bus 的「发送成功≠开始处理」（上游 #106）由这张表补上收件方一侧的账。
读写方 [[bus_delivery_receipt_repository]]。

## 2026-09-09 — `owner_notice_cooldowns` 表（26b，additive）

(agent_id, target, category) 复合主键 + `last_notified_at DATETIME(6)`。把
[[message_bus_trigger]] 的 owner 通知冷却窗从进程内 dict 搬进库：重启不忘、多容器一致、
按 channel 而不是按类别塌缩。读写方 [[owner_notice_cooldown_repository]]；窗口长度仍是
写入方的常量，表只记上次写入时间。

## 2026-09-08（本地 E2E 实测）— `register_table(spec, owner)` 的 owner 改为可位置传参

`hosts.boot` 按 `TableRegistrar = Callable[[Any, str], None]` 位置调用 `register_table(spec, owner)`，而这里把 `owner`
写成了 keyword-only：真实后端里任何带表的用户插件在 boot 时被隔离（`register_table() takes 1 positional argument but 2 were
given`），单测因为一律传 lambda 没抓到。现在两侧契约一致；`tests/plugins/test_hello_world_e2e.py` 新增「用真实注册器 boot」的
回归测试。

## 2026-09-03（批 2a.5）— `skill_catalog.kind` 列（additive，默认 'marketplace'）

`tables.json` 快照只多这一列。

## 2026-09-03（批 2a.4）— `register_table(spec, owner)` + `TABLE_OWNERS` + `plugin_settings` 表

插件表从契约层 `TableSpec` 转成 `TableDef` 进 `TABLES`（同一 `auto_migrate`，不激活也建表）；
`TABLE_OWNERS` 记每张表的 owner（核心表 = `builtin.kernel`），先查「已被别人注册」再查 `ext_<owner>_` 前缀，
同 owner 重复注册幂等。`plugin_settings`（plugin_id+key 唯一）存插件设置行，secret 由平台 store 加密。
`tables.json` 快照只多了这一张表。

## 2026-08-31 — `event_stream.kind` 的长度余量写进注释

新增 `thinking_segment_monologue`（26 字符）之后，`VARCHAR(32)` 只剩 6 个字符
余量。注释里写明了，因为**这个坑的失败方式是不对称的**：超长的 kind 在本地
全绿（SQLite 忽略声明长度），只在 MySQL 上炸（error 1406）。

也就是说本地测试**不构成保护**。加新 kind 前数一下字符。

## 2026-08-28 — `cost_records` 补 `idx_cost_event_id`

`event_id` 列 2026-03 就在，索引一直没建。`chat_history._build_event_meta`
每展开一张运行卡片就跑一次 `WHERE event_id = ?`，聊天面板现在按轮次也这么查
（见 [[MessageBubble]] 同日条目），没有索引就是为几行结果全表扫一遍账本。

幂等性两种方言来源不同，别记混：**SQLite** 的 DDL 生成器发的是
`CREATE INDEX IF NOT EXISTS`；**MySQL 不支持这个语法**，发的是裸
``CREATE INDEX `name` ON …``，幂等靠 `auto_migrate` 事先查
`information_schema.statistics` 的那次预检。所以任何绕开 `auto_migrate`
直接发 DDL 的路径，在 MySQL 上会撞 1061 duplicate key name。

## 2026-08-28（接线）— `channel_ingress_breaker` 现在有**两个**清扫者

本页 2026-08-24 那条写的「保留期由 [[channel_trigger_base.py]] 的
`_run_cleanup` 顺带扫」**已过时**，两处都不再准确：

1. 那条清扫现在**按自己的 `channel_name` 作用域**扫，不再覆盖全表。保留期是
   按 trigger 声明的类属性（与 `DEDUP_RETENTION_DAYS` /
   `AUDIT_RETENTION_DAYS` 并列，Lark 与 Matrix 已经在覆写后两个），不带作用域
   等于「谁先 tick 谁说了算」。
2. 多了第二个清扫者：[[managed_channel_ingress.py]] 的
   `_sweep_breaker_rows`，骑在 ingress 路径上按进程日节流。**必须是两个**——
   基类那条有作用域之后，纯托管部署里没有任何东西会碰这些行。

两者都只删 `tier = 0`，且**都读该渠道 trigger 自己的
`INGRESS_BREAKER_RETENTION_DAYS`**。托管那条第一版读的是基类值，等于把刚立下
的 per-trigger 语义在另一个接收面上再拆一次：原生按 90 天留、托管按 30 天删，
同一张表同一个 channel 两个口径。
# schema_registry.py

## 2026-08-27 — 新表 channel_ingress_breaker

ingress 分级熔断的**层级状态**。一行一个会话键
`agent_id|channel|chat_id|sender_id`，**只在层级变迁时写**：驱动变迁的滑窗
计数和内容指纹留在进程内存里，每条入站消息写一次库是纯写放大，而那些数据
十分钟就过期。必须扛住重启的是**冷却**——8/14 那个乒乓循环跑了 70+ 小时，
期间任何一次重新部署都会把已经隔离掉的对端重新放进来。

`session_key` 列宽 448：四段（128+32+128+128）加三个分隔符是 419，留了余量。
**这个数字不能只写在注释里**——SQLite 的 TEXT 永不截断，MySQL 侧宽度不够只会
在生产上表现为两个 agent 的行在唯一索引上互相覆盖。宽度由
`varchar_width()` 从 DDL 推导并有测试钉着。

`tier_changed_at` 记录 tier **上次变动于何时**，用 guard 自己的时钟。衰减从
它与隔离终点的**较晚者**起算——服刑时间不是沉默，从跳闸时刻起算会把整段隔离
当成良好表现（tier 3 的冷却本身就等于 6 个衰减步）。

**但不能只靠读 `cooldown_until` 来拿这个终点**：半开探测会把该列写成 NULL
（`upsert_state` 是部分写，真的清空），此后行里再无隔离何时结束的记录。所以
探测在清空的同一次写入里把终点搬进 `tier_changed_at`。换句话说这一列的语义
是「tier 上次变动、或隔离上次结束，取较晚」——读它的人不需要再去拼另一列。它存在
是因为两个邻居都不能兼任：`last_tripped_at` 在降级时不变（用它做衰减锚点会
把已付过的沉默再算一遍，衰减过头）；`updated_at` 由仓储用墙钟盖章，而状态机
跑在调用方传入的 `now` 上。一列不可能既是行簿记时间又是状态机时间。

`suppressed_count` 每次跳闸重置，回答的是「**这一轮**隔离吸收了多少」而不是
一个没有意义的终身累计。

保留期清扫已随接线挂进 `ChannelTriggerBase._run_cleanup`（与 dedup / audit
的保留期扫描并列），只扫 `tier = 0` 的行——带升级记忆的行正是要记住的，而它们
能进入清扫面靠的是衰减。源码注释同步改成了陈述句。

## 2026-08-27 — 新表 agent_slot_clear_audit

owner 级「应用默认到全体 agent」会不可逆地删 `agent_slots` 行。删除前把每行
快照（user_id/agent_id/slot_name/provider_id/model/agent_framework/params_json）
append 进 `agent_slot_clear_audit`，让误操作**可追溯、可据此恢复**（DB 留痕胜过
只写日志——docker 日志会在 restart 时轮转）。当前只有写入方（`_clear_one_slot`），
**没有只读查询入口、也没有保留策略**——恢复是人工/脚本按审计行回写，不是自动的；
需要时再补读侧 API + TTL。由 [[slot_service]] 的批量清除写入。双方言列已填全，
四个快照列可空（NULL 原样透传）。

## 2026-08-26 — `narrative_routing_audit` 的合并路由八列(纯增量)

`merged_call` / `merged_verdict` / `merged_ms` / `merged_input_chars` /
`merged_truncated` / `anchor_bm25_rank` / `anchor_raw_score` /
`anchor_in_menu`,双方言齐备、**全部可空**。可空的两个理由与
`pool_is_shadow` 一样:prod 存量行早于它们(铁律 #6,活表上 `NOT NULL`
无默认值的 ALTER 会失败),而且对每一个按它们过滤的查询来说,NULL 与 0 的
含义本来就该不同 —— NULL 是"这一行不是合并路径决策的"。

**`retrieve_ms` 现在有第三个人群,必须一起写下来**:两次调用路径上它
**包含** `judge_ms`;影子行上它只有 tier-2(仪器自耗);**合并行上它也只有
tier-2** —— 因为那条路上 LLM 有自己的列(`merged_ms`)。于是"tiers 2+3 合计"
的过滤条件是 `COALESCE(pool_is_shadow,0) <> 1 AND COALESCE(merged_call,0) <> 1`——
必须 NULL 安全:`= 0` 会把部署前的 NULL 行全部滤掉(即整个基线窗口),
两次调用路径写的是 0/空串,只有部署前的行才是 NULL(round 5 I1)。
一列三个量纲已经是共享的极限,**下一批要拆 `retrieve_self_ms`,不要再加第四个**
(工单 `todo/2026-08-25-retrieve-ms-nests-judge-ms.md`)。钉子:
`test_retrieve_ms_on_a_merged_row_is_the_bm25_pass_alone`(慢 stub,断言慢调用
不漏进 `retrieve_ms`)。

`merged_ms` 的列注释里明写**自耗时、不嵌套任何东西**,是因为 `retrieve_ms`
确实嵌套 `judge_ms`,而三列并排都叫 `*_ms`、没有任何地方写着谁包着谁,已经
把两位读者送到同一个错误结论(工单
`todo/2026-08-25-retrieve-ms-nests-judge-ms.md`)。并排的耗时列要么互斥,
要么显式声明嵌套。

## 2026-08-26 — 影子行改变 retrieve_ms 的量纲(PR #365 review I2)

`pool_is_shadow=1` 的行上 `retrieve_ms` 只含仪器自身的 tier-2 耗时
(judge 未运行);跨人群成本聚合必须先按 `pool_is_shadow` 过滤,否则续接轮
多数人群的 ~13ms 会稀释仲裁成本读数。`keyword_ms` 是唯一两人群定义相同、
可直接比较的成本列。in-code 契约注释(本文件与 models.py)已同步此语义。
(合并路径上它是第三人群——见上一条。)

## 2026-08-25 — `narrative_routing_audit.pool_is_shadow`(纯增量)

`INTEGER` / `TINYINT(1)`,可空。标记"这一行的池是只记录、没决策的"(续接轮)。
可空有两个理由:prod 存量行早于它(铁律 #6,活表上 `NOT NULL` 无默认值会失败),
以及对任何过滤它的查询来说 NULL 与 0 同义。

语义细节与"为什么 `gate_short_circuit` 保持 NULL"见 [[models.py]] 的 8-25 条。

**"可空 / NULL 与 0 同义"针对的是读侧,不是写侧**(2026-08-26 review #1):
写侧永不产生 NULL(`RoutingAudit.pool_is_shadow` 是普通 bool,没有
`bypass_score_gate` 那种"这一轮没跑"的第三态),可空**只为存量行**;
于是读侧的过滤查询必须把 NULL 与 0 当同一件事。两句都成立,针对的不是同一侧。
完整对照表见 [[narrative_routing_audit_repository.py]] 的 8-26 条。

## 2026-08-25 — `channel_ingress_breaker.session_key` 改 VARCHAR(448)、键含 agent_id

键是四段 `agent_id|channel|chat_id|sender_id`。
**419 = 128 + 32 + 128 + 128 加三个分隔符**，列宽取 448 留头寸。
（本页 2026-08-24 那条写的 `VARCHAR(320)` / 「三段加两个分隔符」已过时。）

原本漏了 `agent_id` 会造成误熔断，见
[[channel_ingress_breaker_schema.py]] 与 [[ingress_guard.py]]。

**列宽窄了是 SQLite 看不见的 bug**（这条教训保留）：TEXT 永不截断，本地
全绿，只有 MySQL 侧才会因长键截断让两个 agent 的行撞唯一索引互相覆盖。
现在 `session_key()` 还对四个分量各做 128 的钳制，让 419 这个算术由构造
保证而不是靠「平台不会给超长 id」的假设。

## 2026-08-24 — `channel_ingress_breaker` 新表（ingress 熔断器的持久状态）

注册 `channel_ingress_breaker`：[[ingress_guard.py]] 的落库那一半，每个会话键
`agent_id|channel|chat_id|sender_id` 一行。

**只在层级变迁时写。** 驱动变迁的滑窗计数和内容指纹留在进程内存——为 10
分钟就过期的数据每条入站消息写一行是纯写放大。必须活过进程的是**冷却**：
8/14 那个乒乓循环跑了 70+ 小时，期间任何一次重新部署都会把已经隔离 24 小时
的对端重新放行。

列：`session_key`(**当时**为 VARCHAR(320)、三段；2026-08-25 改为 VARCHAR(448)、四段含 agent_id，见本页最新条目)、
`channel`(VARCHAR(32))、`agent_id`/`chat_id`/`sender_id`(VARCHAR(128))、
`tier`(INT，0=闭合，N=已跳闸 N 次的升级记忆)、`cooldown_until`(DATETIME(6))、
`suppressed_count`(INT，**每次跳闸清零**，所以它回答「这一次隔离挡下了多少」)、
`last_reason`(VARCHAR(64))、`last_tripped_at`、`created_at`/`updated_at`。
索引 `uk_channel_ingress_breaker_key`(unique)、`..._cooldown`、`..._updated`。

保留期由 [[channel_trigger_base.py]] 的 `_run_cleanup` 顺带扫，**只删
`tier = 0` 的行**——带升级记忆的行正是我们承诺要记住的东西。

**坑（清扫实现踩过）**：sqlite 把 `updated_at` 回读成
`2026-08-24T10:29:33.197094+00:00`（isoformat 默认 'T'），与
`channel_seen_messages` 那种空格形式的 cutoff 字符串比较时排序全反，
`updated_at < %s` 一条都匹配不上却静默报告删了 0 行。时间比较改在 Python
里用 `dialect_time.event_time_str` 做。

## 2026-08-21 — events 加复合索引 `idx_events_user_state`

服务 [[run_recorder.py]] `first_live_run_id` 的 `(user_id, state)` 查询
（回收器每轮每个候选问一次）。没有复合索引时 MySQL 会选 `idx_events_user_id`
再逐行过滤 state，长期用户等于每次都为自己的全部历史买单 —— 越重度的账号越
吃亏，而 events 从不清理，只会越来越长。纯新增索引，`auto_migrate()` 幂等
补上，不触铁律 #6。

## 2026-08-20 — `narrative_routing_audit` 加两列(纯增量)

`bypass_score_gate INTEGER/TINYINT(1)` + `bypass_reason TEXT/VARCHAR(32)`,
两个 dialect 都填了,两列都可空。

**可空是硬要求,不是风格**:prod 上这张表已有 26,922 行早于这两列,
`ALTER TABLE ADD COLUMN ... NOT NULL`(无默认值)在活表上会失败。
铁律 #6 的"只做增量"在这里的具体形态就是这个。
`VARCHAR(32)` 对得上七个短码里最长的 `participant_present`(19 字符),
测试里钉了这个宽度 —— MySQL 非严格 sql_mode 下超长是**静默截断**,
而这一列正是下一轮标定的 GROUP BY 键。

## 2026-08-19（PR#327 审后）— gateway_key_misuse.user_id 收窄到 VARCHAR(64) + `varchar_width` helper

- **列宽 128→64（I2，武装前必改）**：`user_id` 原为 `VARCHAR(128)`，与全仓 id 规范不符
  （`users.user_id` = `VARCHAR(64) UNIQUE`，`ban_audit` / `user_notifications` 也都是 64）。
  128 会让端点「超宽→反解失败落 NULL alert-only」的兜底阈值也变 128，于是 65~128 字符的
  值被当**权威归因**落库、伪造出下游查不到的可处置 id。收窄到 64 让「太长以致不可能是真
  id」的判定与列宽一致。**趁表尚未在 dev auto_migrate 建过收窄**（铁律 #6：建过就不能再窄）。
- **`key_hash` 保持 256**：那是 hash 不是 id，不收窄。
- **新增 `varchar_width(table, column)`**：解析列的 `VARCHAR(N)` 宽度，作为**单一真相源**。
  gateway-key-misuse 端点的逐字段截断宽度与 `user_id` 阈值都从它取，不再各写一份硬编码
  数字（会与 DDL 漂移）；非 `VARCHAR(N)` 列会在 import 期就抛错而不是静默截断到错误宽度。

## 2026-08-19 — gateway_key_misuse 新表（网关 key 异常使用事件的权威落库）

注册 `gateway_key_misuse`：网关 key 的异常/越权使用事件的结构化记录表，供安全监控消费。
归因 100% 走权威结构化信号，绝不 grep 日志文本。

**单一写方 = backend 内部 admin gateway-key-misuse 端点**（[[gateway_key_misuse.py]]，与
[[suspend.py]] 同一把 `X-Admin-Secret` 锁）。写入的 `user_id` 是调用方（网关是「这把 key
绑定了哪个身份」的权威）**权威反解**出来的结果，端点本身**不解析任何文本**、只记录被交给
它的字段。executor/agent 无 admin-secret 凭据 → 无法写这张表。**读方是安全监控（只读）**，
据此驱动响应梯子。

列：`id`(BIGINT UNSIGNED 自增主键，兼作监控的 watermark PK)、
`user_id`(VARCHAR(64)，**可空**，对齐 `users.user_id`)、`run_id`(VARCHAR(128))、`key_hash`
(VARCHAR(256))、`caller_ip`(VARCHAR(64))、`caller_ua`(VARCHAR(256))、
`model`(VARCHAR(128))、`hit_at`(DATETIME(6) NOT NULL，sqlite 侧
`(datetime('now'))` → MySQL `CURRENT_TIMESTAMP(6)`)、`disposition_status`
(VARCHAR(32) NOT NULL DEFAULT `'pending'`)、`created_at`(同 hit_at 形制)。
索引 `idx_gateway_key_misuse_user(user_id)`、
`idx_gateway_key_misuse_status(disposition_status, created_at)`，以及**唯一**索引
`idx_gateway_key_misuse_dedup(key_hash, hit_at)`——幂等键：调用方传权威事件时间时，
「写成功但响应超时」的重试携带同一 `(key_hash, hit_at)`，落回同一行而非重复行（硬信号
一次即处置，重复行会重复处置）。`key_hash` 为 NULL 的未解析事件不参与去重（SQL 唯一性
视 NULL 互不相等，alert-only 行永不误去重）。

**`user_id` 可空是刻意的**：上游反解失败（网关抖动 / key 已被删）时，端点仍落一行
**alert-only** 记录、`user_id=NULL`，由人工分诊；响应梯子**绝不**在 NULL id 上触发，因此
永不伪造一个可处置的归因 id。设成 NOT NULL 会逼写入方编造哨兵值，把「未解析」和「解析为
某人」两件正交的事混为一列。

纯新增表（铁律 #6），`auto_migrate` 幂等建它。双方言由同一份 TableDef 生成，
`tests/utils/db/test_gateway_key_misuse_schema.py` 直接对 `generate_sqlite_ddl` /
`generate_mysql_ddl` 断言两方言 DDL 均成立（含 DATETIME 默认值的 MySQL 翻译）。

## 2026-08-18 — TYPE_CHECKING 导入 `DatabaseBackend`（F821 配套，零行为变化）

`auto_migrate` / `_self_heal_missing_tables` / `_verify_all_tables_present`
的字符串注解 `"DatabaseBackend"` 此前没有任何导入支撑；本 PR 启用 ruff F821
（注解里的未定义名会被拦）后补了 `if TYPE_CHECKING:` 导入。这些注解在
`from __future__ import annotations` 下是纯字符串、运行时从不求值，所以导入
只需 type-only。顺手删掉了 `auto_migrate` 函数体内那个遗留的延迟导入
（原 `# noqa: F811` 行）——函数体内对该名字零使用，是死代码。行为零变化。

## 2026-08-17 — inbox 拿到自己的两张表（记录层，与 bus 解耦）

`inbox_threads` + `inbox_thread_messages`。inbox 此前住在 `bus_messages` /
`bus_channel_members` 里，prod 实测两笔代价：

- **`bus_messages` 里 86% 不是 bus 消息**（28,605 / 33,164 行是 IM inbox）。表名描述的
  是它的少数派。
- 写入方为了让面板找得到 thread，**给 agent 建了 `bus_channel_members` 成员行**，而
  **没有任何人推进它的 `last_read_at`**（172 个 IM 成员行里 159 个游标为 NULL，92%）。
  bus 的未读判据是 `created_at > COALESCE(last_read_at, epoch)`，于是 **1,364 条 IM
  历史永久「未读」**，以伪 agent `lark_user_<id>` 的名义灌进 **90 个 agent** 每一轮的
  上下文。

**分表让新写入的行结构性地到不了**：agent 的未读注入读的是
`bus_messages JOIN bus_channel_members`，而记录层写的是这两张表，所以新行不在那里。

**但旧行还在。** 每个已部署的库里都留着旧写入器写进 `bus_messages` 的 IM 历史，搬表对它们
无效，未读谓词照样会把它们交给模型 —— 上面那 1,364 条说的就是这些行。所以
[[local_bus.py]] `_unread_predicate` 加了一道按 dedicated-trigger 前缀的过滤，注入才在部署当天
停下；清理是回填 runbook 里的手动步骤，清完这道过滤才能退休。

本节原先写的是「**不是过滤器**——过滤器正是 2026-07-03 事故的成因」。（订正于 2026-08-18，
第三轮预审。）那句话把读者引向删掉那道过滤，而它是唯一挡住投毒的东西、删掉不报错。
2026-07-03 的教训没作废，但它针对的是**手维护**的列表：现在这张由 registry 推导，且带一个
退休条件。

**分界线是 operational vs observational**：bus 表承载投递机制（游标、待处理、路由），
这两张承载「人要读的记录」。因此 `inbox_threads.last_read_at` 是**用户的**阅读状态，
对 agent 拿到什么**毫无影响**——这两件事此前是同一列，所以用户在面板上点一下「已读」
就改变了 agent 下一轮的上下文。

### `source_message_id` 是回填的幂等保证，不是备注

Owner 决策（2026-08-17）：历史**回填**，且**由 Owner 在部署后手动执行**。于是新写入
路径在脚本开始前就已经在写这张表了，重叠窗口会让其中每条消息**重复出现在用户看得见的
界面上**。

这一列**是幂等键该待的地方** —— 放进脚本就是放在一个会被忘记的地方。列可空：只有回填行有值，
live 写入没有对应的 bus 行，NOT NULL 会逼写入方编一个 id，而那个 id 迟早和真的撞上。

**两件本节原先说错、必须写清的事**（订正于 2026-08-18，第三轮预审；源码注释已同步）：

1. **唯一索引不会把重复插入变成 no-op —— 它会抛错。** 原文写的是「无论脚本跑几遍、时间窗
   猜得多离谱都是 no-op」。没有 `INSERT IGNORE`、也没有 `ON CONFLICT`。脚本必须自己捕获重复键
   后重读（与 `InboxRecorder._ensure_thread` 处理建会话竞态同形，且要**用重读判断而不是匹配
   驱动异常类型** —— aiosqlite 与 aiomysql 的异常类不同）。照原文写脚本，会在第一个重叠处
   中途抛错、把一个用户可见的界面回填到一半。
2. **现在没有任何调用方写这一列。** `_insert_message` 的参数默认 `None`，所以唯一索引目前盖在
   一个全 NULL 的列上（两个方言都允许多个 NULL，所以不报错，也就没人发现）。填它是回填脚本
   的职责，而脚本按 owner 决定是部署后手动写的、尚不存在。

→ 完整回填步骤与验证清单：`reference/self_notebook/todo/2026-08-17-inbox-backfill-runbook.md`
→ 设计全文：`reference/self_notebook/specs/2026-08-17-conversation-harness-redesign-design.md`

## 2026-08-14 — 差事层与 job 来源面：三列两索引

`team_work_items.origin`（tool|auto）：owner 2026-08-07 的分层决定第一次可执行，
语义见 [[team_work_schema]]。默认 `tool`，让历史行保持它本来的含义。同批两条索
引服务 [[errand]] 仅有的两个热读。

`instance_jobs.origin_source` / `origin_channel_id`：job 记住**它是在哪儿被要求
的**，好让结果回到那儿——PR #230「回复面跟随来源」在 job 面的延伸。空 = owner 私
聊，既是历史行为、也是**唯一永远存在**的投递面，所以兜底不需要特例。

拆成两列而不是一个 `"message_bus:ch_x"`：source 选代码路径，channel 是它的参数，
合成一个字段会让每个读者各自再解析一遍。
## 2026-08-14 — `narrative_routing_audit` 新增四列 per-tier 耗时

`continuity_ms` / `retrieve_ms` / `keyword_ms` / `judge_ms`，**可空是刻意的**：
NULL = 这一层没跑。短路的决策跳过 judge，那里存 0 会把"仲裁有多贵"答得远低于真实值，
而这几列存在的意义就是支持这个对比。纯新增可空列，`auto_migrate()` 幂等加列。

## 2026-08-14 — 曾加过两张延迟观测表（`bus_hop_timing` / `turn_timing`），已撤回

记下来免得有人再走一遍。

**加表时的论据是错的**：当时引「教训 #5：日志会轮转，用 DB」，但那条针对的是
`docker logs`。本项目早已按服务把日志写文件，`rotation="00:00"` +
`retention="30 days"`（`utils/logging/_setup.py`）。

**日志实测够用**：一行 `grep + awk` 从日志算出的分位数与表版本报表**逐位一致**。
`scripts/diag_collector/latency_report.py` 现在直接解析日志，产出相同。

**取舍**：表买到 JOIN 和自解释 SQL，代价是两表 + 两 repository + 两处热路径写入 +
迁移。为一次延迟排查不划算。真要长期盯，该建的是 `turn_timing`（秒数在 setup 段），
不是这一整层。

**保留的例外**：`narrative_routing_audit` 的四个 `*_ms` 列留着——那张表本来每次决策
就写一行，加可空列边际成本为零，而「成本 ↔ 决策」的关联是日志给不了的。

## 2026-08-13 — ban_audit 新表（账户状态变更审计）

注册 `ban_audit`：账户状态变更的追加式审计表，一次 suspend / reinstate 一行。
列：`id`(BIGINT UNSIGNED 自增主键)、`user_id`(VARCHAR(64) NOT NULL)、
`action`(VARCHAR(32) NOT NULL)、`reason`(MEDIUMTEXT)、`evidence_ref`(MEDIUMTEXT)、
`actor`(VARCHAR(128))、`prev_status`(VARCHAR(32))、`created_at`(DATETIME(6)
NOT NULL，sqlite 侧 `(datetime('now'))`)。索引 `idx_ban_audit_user_id(user_id)`，
按 user_id 查询。

**`prev_status`（2026-08-13 追加列）**：记录该行动作发生**前**账户的状态（不透明，
就是一个 `users.status` 值）。additive 列——`auto_migrate` 在既有部署上增量补列，
无破坏性迁移、不触铁律 #6。suspend 写它替换掉的状态、reinstate（含被 409 拒绝的
那次）写它试图恢复前的状态，让「reinstate 从什么状态翻回来 / suspend 覆盖了什么」
可追溯，而审计表仍不携带任何策略词汇。

`reason` / `evidence_ref` 是调用方提供的**不透明自由文本**（绝非 enum），本表因此
不携带自己的策略词汇；`actor` 记录是谁做的变更。追加式、只增不改。写入方是
[[ban_audit_repository]]（best-effort，advisory），真相源是 `users.status`。

## 2026-08-11 — reply_language:回复语言偏好落库并注入 system prompt

`user_settings` 新增 `reply_language`(VARCHAR(16),NULL=从未设置=模型自由)。auto_migrate 增量加列,无危险变更。

## 2026-08-11 — channel_narramessenger_credentials gains idx_nm_cred_profile

Added a non-unique `Index("idx_nm_cred_profile", ["nexus_profile_id"])`
(named to match the table's `idx_nm_cred_*` siblings; renamed pre-release
from the initial `idx_narramessenger_profile` — never shipped, so no
migration concern)
to back the credential manager's new `get_by_profile_id` reverse lookup (the
prewarm endpoint resolves an agent by the platform's `agent_profile_id`).
Non-unique on purpose: the column existed since inception but `do_bind` never
wrote it until this same change, so every pre-existing row has
`nexus_profile_id == ""` — a unique index would reject the second empty-string
row on insert. See the credential manager's mirror doc for the back-fill gap
this leaves (old rows need a rebind to become resolvable by profile id).

## 2026-08-10 — product facts + exact provider source

Added append-only `product_analytics_events`, indexed by event/user/run/failure
time. No external telemetry history is migrated. `cost_records` gained
`provider_card_source`; the older resolver branch is always `user` now and
cannot distinguish `netmind_free` from another user-owned provider. The new
column deliberately reuses the existing `created_at` range index instead of
building a composite index during startup; this avoids an unbounded full-table
index build delaying `/health` on the high-volume cost ledger.

The product fact hot-retention contract is 400 days, covering the data API's
maximum 365-day analysis window plus operational margin. Enforcement belongs
to deployment-managed archival/cleanup, never `auto_migrate`: schema startup
must not perform destructive or duration-unbounded maintenance.

## 2026-08-07 — team_work_items 新表 + teams 的巡查列

工作板:产品里第一个**任务级**对象(此前持久化的一切都是对话)。语义与状态机
见 [[team_work_schema]]。`channel_id` 从 team_id 反范式化是为巡查:候选查询每轮
都要「把 lead 唤到哪个房间」,逐项 join 太贵。索引 `(team_id, status)` 是巡查
唯一热路径,`(root_run_id)` 服务停止→暂停。

`teams` 加四列:`patrol_enabled`(NULL = 未定,对**有 lead** 的 team 读作开 ——
设 lead 这个动作本身就是在说「这个负责」)、`last_patrol_at`,以及
`patrol_spoke_at/count`。

**后两列刻意落盘**:bus 已有 per (agent,channel) 限流,但它活在
`MessageBusTrigger._rate_counters` —— 内存 dict + `time.monotonic()`,workers
一重启就清零。对它原本的用途(压制话痨 agent)没问题;对巡查**不行**:巡查
消息被豁免了级联深度上限(拍板口径 a),这个计数器是仅剩的兜底,而一重启就
消失的兜底不是兜底。

## 2026-08-07 — 审计两表的保留期：待定，不是「不需要」

`narrative_routing_audit` 每轮一行、带 ~15KB 的 `candidates_json`；
`narrative_text_snapshots` 按内容寻址增长（去重让它长得慢，但**没有上界**）。

项目里 `service_audit` / `instance_executor_audit` 同样没有清理策略，所以这不是本
次引入的新问题——但这两张表的单行体量比它们大一到两个数量级。**保留期尚未决定**，
需要 Owner 定策略（清理 worker？按天分区？只留最近 N 轮？）。在那之前它们无限增长，
先写明，免得半年后靠磁盘告警才发现。

## 2026-08-07 — narrative_routing_audit + narrative_text_snapshots（E1）

narrative 路由的决策轨迹。`candidates_json` 存**整个** BM25 候选池而非 top-K，每条
指向一份内容寻址的文本快照——这不是保险起见：`bm25_rank` 的 IDF/avgdl 在候选集自身
上算，裁过的池子重放不出原来的分数；而被打分的文本本身又被异步 LLM 更新几乎每轮
覆写且不留历史。两条约束叠加，"只存 id 和分数"的审计等于不能重放。

`narrative_text_snapshots` 按 sha256 内容寻址去重：相邻两轮通常只有主 narrative 的
摘要变了，所以 100 条候选的池子每轮只新增约 1 行。详见
[[narrative_routing_audit_repository.py]]。

## 2026-08-07 — events.root_run_id / bus_messages.root_run_id + 索引

触发树标签。**从根继承的扁平标签,刻意不是父子指针**:这里唯一被问到的问题
是"哪些 run 属于用户要停的这棵树",标签一条索引查询就能答,任意深度;父子边
额外提供的是"遍历树"的能力,级联停止不需要(协作拓扑图才需要,那是另一个
需求)。

- `events.root_run_id`:根 run 存自己的 event_id,被引发的 run 继承同一个值。
  由 [[run_recorder]] 在 late-bind 时与 running 翻转同一条 UPDATE 写入。
- `bus_messages.root_run_id`:发送方那一轮的树,把血缘带过唯一会丢的那一跳
  (agent 问同伴,写的是一条新消息)。
- `Index("idx_events_root_state", ["root_run_id", "state"])`:级联唯一的热
  路径("这棵树里还在跑的 run"),复合是因为写旗标必定同时过滤这两列。

## 2026-08-07 — events 增加 cancel_requested_at 列

可空 DATETIME(6),owner 请求停止这个 run 的时刻。纯新增列,
`auto_migrate` 下次启动自动加上,不改任何既有列语义(铁律 #6)。

**为什么是时间戳而不是布尔**:这个旗标要回答三个问题,只有时间戳能全部
回答 —— 是否有待处理的停止(非空)、重复点击是否无副作用(幂等写)、
这次请求是否属于**当前**这个 run(`started_at` 早于请求时间)。第三点是
关键:旗标活在长寿的 events 行上,布尔值无法区分"给这一轮的"和"上一轮
遗留的",后者会杀掉无辜的后继 run。

写者:`backend/routes/runs.py`。读者两个:[[cancel_watcher]](持有 token,
据此触发)和 [[run_recorder]] 的 `sweep_stale_runs`(据此把停止中的 run
落成 cancelled 而非 failed)。

## 2026-08-07 — team shared working-space：一列 + 两张新表（全部 additive）

**`instance_artifacts.team_id`（可空）**：artifact 此前只有 agent/user 两个维度，这正是
「team turn 里注册的产出只能落进某个 agent 私有列表」的机制原因。NULL = 私有，涵盖所有
存量行与非团队回合，故私聊语义按构造不变、无需回填（铁律 #6）。`agent_id` 保留不动——
归属迁到 team 之后，「谁产出的」仍要答得出。配套 `idx_artifact_team_updated`，否则团队面板
与 agent prompt 的团队半边是全表扫描。

**新表 `team_files`**：共享目录（`_shared/teams/{id}`）此前磁盘有文件、库里无行，落地用的是
生成的 file_id 当磁盘名，`original_name` 只活在内存 dict 里 → 无法列举，只能靠 agent 在群里
念路径。`content_hash`(sha256) 是去重能成立的前提：**同名不等于同一个文件**。同名同 hash =
真重复，复用既有行；同名不同 hash = 不同文件，**两份都留**（静默覆盖属破坏性写）。唯一索引
带上 hash 正是为了让后者仍可插入，同时让去重在并发下也成立。选 sha256 而非 md5：日后 CAS
复用同一哈希，且成本相同。另有 `idx_team_files_prefilter`——hash 要读全文件，写路径只在
(team,name,size) 撞车时才算，那次查找必须有索引否则「廉价前置」本身就是全表扫描。

**新表 `instance_artifact_history`**：只记 who/when/where-it-pointed，**不存内容**，与
2026-07-21 因成本退役的 `instance_artifact_versions` 不是一回事（测试 `test_retired_versions_table_stays_retired`
专门守这条）。一行几十字节，故常开且不依赖 agent 配合——归因必须在模型不配合时也正确。
`event_id` 沿用本项目既有的 turn 句柄语义（同 [[bus_messages]]），可空：MCP 注册路径目前
拿不到它。

## 2026-08-04 — bus_messages 增加 sender_turn_source 列

可空,记录发送方那一轮的种类(owner 面 vs message_bus)。纯新增列,
`auto_migrate` 下次启动自动加上,不改任何既有列语义(铁律 #6)。用途见
[[message_bus_trigger]]:收件方该"答同伴"还是"回报 owner",只能靠消息级
事实判断。

## 2026-07-31 — bus_messages.event_id (additive)

The `events` row id of the turn that produced an agent reply, stamped by the
trigger's team branch at post time. NULL for user messages and legacy rows.

> ⚠️ 「stamped by the trigger's team branch」已于 2026-08-14 失效 —— 见本文件
> 末尾的 08-14 节。
Powers the transcript's per-message "view reasoning & tools" disclosure —
unlike `bus_agent_activity.event_id` (one row per member, latest turn only),
this one gives every historical message its own handle.

## 2026-07-30 — bus_agent_activity.event_id (additive)

Binds the activity mirror row to the `events` row of its current/most
recent turn, so the team-room UI can fetch the finished turn's full
event_log through the existing event-log endpoint. Written by
`TurnActivity.note_event_id`, reset on `start()`, kept after `finish()`
— see [[_bus_activity]] for the full lifecycle reasoning.

## 2026-07-30 — 新表 model_probe_ledger / model_probe_suspects

探测 ledger 落 DB（一行一 source，models_json 与 committed 快照 per-source 同
形）+ 运行时模型嫌疑表（(source, model_id, protocol) 唯一，occurrences 计数）。
背景见 [[model_probe_ledger]] 与 [[model_health]]。

## 2026-07-29 — 摘掉 agent_cli_sessions 注册(T7)

`# 17. agent_cli_sessions` 的 `_register(TableDef(...))` 整块删除。表的用途是存
可 resume 的 CLI 会话句柄,而 [[transcript]] 让 adapter 每轮自己写 transcript、
用完即删,没有句柄要存。该机制**从未上线到 feature branch 之外**。

**没有写 DROP,是刻意的。** `auto_migrate()` 只建表/加列/加索引,从不删——所以摘掉
注册之后,新库不再创建,而跑过旧代码的开发库会留一张空的孤儿表。留着:一张空的
无用表无害,而把 `DROP TABLE` 提交进仓库就是往仓库里放破坏性迁移(铁律 #6)。
需要的话手工删。

## 2026-07-29 — lark_credentials.bot_open_id

Additive column holding the bot's own Lark open_id. Consumed by
[[lark_trigger]]'s group @-mention gate; written by
[[_lark_credential_manager]] `update_bot_identity` from the same
`/open-apis/bot/v3/info` response that already supplied `bot_name`.
`auto_migrate()` adds it in place; existing rows read back empty and the
gate falls back to display-name matching for them.

## 2026-07-28 — agent_cli_sessions.narrative_id 加宽到 VARCHAR(128)

原为 `VARCHAR(64)`，与规范宽度（`narratives.narrative_id` = `VARCHAR(128)`，
`events` / `instance_narrative_links` / 报表表也都是 128）不一致。纯加宽，
`auto_migrate` 幂等改列，不触碰铁律 #6（没有收窄、没有语义变更）。
不修的后果不是报错而是**静默失效**：MySQL 侧一旦 id 超过 64 字符就截断写入，
之后每一次比对都不相等，于是 resume 永远命中不了、只会一直冷启动——而这条路径
本来就设计成"任何存疑即回落冷启动"，所以不会有任何告警。列注释已写明这个理由。

## 2026-07-28 — `bus_agent_activity.steps`

Added a nullable `TEXT` / `MEDIUMTEXT` column holding the CURRENT turn's phase
transitions as `{"items": [{phase, at}], "dropped": n}`, reset on
`mark_running` and kept after the turn ends. It backs the team chat's per-agent
step timeline without standing up a second events pipeline; `auto_migrate` adds
it in place, and no existing column changes type or meaning.

## 2026-07-27 — instance_gateway_session_keys.metered_at

Added `metered_at DATETIME(6) NULL` + index (`status`,`metered_at`). Set once the
run's real token usage was summed from the gateway and deducted from quota
([[gateway_spend_reconciler]]); NULL = not yet metered (idempotency guard). The
new index backs the reconciler's revoked-but-unmetered scan.

## 2026-07-24 — instance_gateway_session_keys

Added `instance_gateway_session_keys` — the ledger for per-run LiteLLM gateway
session keys ("会话票") backing the free tier. Columns: `run_id` (= the gateway
`key_alias`, the only revoke handle) / `user_id` / `agent_id` / `key_hash`
(LiteLLM's non-secret token hash — the usable secret is NEVER persisted) /
`status` (active|revoked) / `created_at` / `revoked_at`. Indexes:
unique(`run_id`), (`status`,`created_at`), (`user_id`). Written on mint, flipped
on revoke (and by the executor-reaper hook for crash orphans of idle users).
Security-critical: a leak of this table cannot be replayed against the gateway
(no usable secret at rest). See
[[gateway_key_service]] / [[gateway_session_key_repository]].

## 2026-07-25 — 新表 agent_cli_sessions(resume 化 R1:句柄持久化)

runtime 归属的新表(**不带 instance_ 前缀**——那是模块私有表约定),一行 =
一个可 `--resume` 的 CLI 会话句柄,唯一键 `(agent_id, platform_session_id,
framework)`。载荷:`cli_session_id`(ResultMessage.session_id)+ 三个有效性锚
(`narrative_id` / `config_fingerprint` / `working_path`,任一不符 → 冷启动)。
纯 additive;cli_session_id **不进 cost_records**(一期 T1.1 GOTCHA 维持)。
读写方:[[cli_session_repository]];计划:
`reference/self_notebook/plans/2026-07-25-agent-loop-resume.plan.md`(R1 只捕获
不 resume,零行为变化)。

## 2026-07-23 — cost_records 加 prompt-cache 埋点三列(W1)

additive:`cache_read_input_tokens` + `cache_creation_input_tokens`(BIGINT
UNSIGNED,NOT NULL DEFAULT 0,同 user_quotas 的 used_* 形制)+ `num_turns`
(INT,**nullable 是刻意的**:NULL=框架未上报,与上报 0 区分)。动机:input_tokens
一个总数分不出全价/缓存写(1.25×)/缓存读(0.1×),缓存是否生效完全不可见
(token 诊断报告 R5)。写入见 [[cost_tracker]] 同日条目;计划:
`reference/self_notebook/plans/2026-07-23-token-consumption-optimization.plan.md`。

## 2026-07-22 — bus_agent_activity

Added `bus_agent_activity` (composite PK agent_id+channel_id) — a lightweight live-status
mirror for team-room agent runs (state/phase/tool_count/heartbeat). Written by the trigger,
read by the team-chat status view. See [[_bus_activity]].

## 2026-07-21 — teams.lead_agent_id

Added nullable `lead_agent_id VARCHAR(64)` to `teams` — the agent that answers a team-chat
message with no @mention (NULL = earliest-joined member fallback). auto_migrate adds it
idempotently. See [[teams]].

## 2026-07-20 — bus_messages.attachments

Added nullable `attachments TEXT` to `bus_messages` (JSON list of bus-attachment
dicts). `auto_migrate()` back-fills the column idempotently; no destructive change.
See [[_bus_attachment_impl]] for the multimodal-A2A feature it backs.

## 2026-07-22 — 计费可审计化：cost_records +user_id/provider_source，新增 quota_deductions

`cost_records` 加两列（additive、nullable）：`user_id`（VARCHAR(128)，对齐
`user_quotas.user_id` 避免 join 截断）+ `provider_source`（VARCHAR(32)），并加
`idx_cost_records_user_id`。此前只能靠 `agent_id → agents.created_by` 追用户，
agent 硬删即断链。新表 `quota_deductions`（逐笔扣减流水，自审计冗余
provider_source/model/agent_id）：`user_quotas` 只有累计标量，无法定位/退还单笔
错扣。写入见 [[cost_tracker]] / [[quota_repository]]；历史回填见
`scripts/data_migrations/backfill_cost_records_user_id.py`。

## 2026-07-21 — team_catalog 表(Team Marketplace)

additive:catalog INDEX,一行一个 team/agent bundle 模板;store_key 指向
artifact store 里的 .nxbundle(独立 prefix),bundle_sha256 防篡改。unique
template_id + (enabled, sort_order) 索引。cloud registry 写,desktop 空表。

## 2026-07-21 — skill_catalog.is_default 列(stage 9)

additive TINYINT(1) 默认 0:标记「建 agent 时自动安装」的默认技能。
manifest 的 `"default": true` 在 publish 时写入。

## 2026-07-20 — Skill Marketplace: 4 new tables

Registered `skill_catalog` (cloud-authoritative marketplace directory, one
row per skill_id × version, UNIQUE(skill_id, version)), `skill_installations`
(per-workspace audit follower of the filesystem skill state,
UNIQUE(agent_id, user_id, skill_id) — note the triple: workspaces are
`{agent_id}_{user_id}`), `skill_scan_results` (append-only scan runs,
non-unique (skill_id, version) index on purpose — latest row by id wins),
and `team_skill_policies` (placeholder; Team Recommended phase adds logic).

All four exist (empty) on desktop deployments because auto_migrate is
unconditional; only the cloud instance writes catalog/scan rows. Purely
additive.

## 2026-07-16 — user_providers 加 netmind_account_id / netmind_account_email

两列 additive、nullable:铸 NetMind key 时(netmind_provisioner)捕获的账户身份
(user_system_code + email),供 Settings 显示"该充哪个账户"。非 NetMind 行与旧行留 NULL。
非密——绝不存登录 JWT。见 `netmind_provisioner.py.md` 与
`.mindflow/project/references/netmind_billing.md`。

## 2026-07-15 — MCP 管道改名 `mcp_urls`/`mcp_server_urls` → `mcp_servers`

值类型从 url 字符串升级为 spec 对象 `{"url": str, "headers": {str:str}?}`，
支撑用户 MCP 自定义请求头（Authorization 等）贯穿全链路。本文件仅机械跟随
改名/类型，职责不变。

## 2026-07-13 — Agent 实时层熔断器接入

注册新表 `instance_agent_circuit_breaker`（实时层 Agent 熔断状态，键 agent_id，双方言，additive auto_migrate 落为新表）。列：cb_status/consecutive_failure_count/failure_category/cooldown_until/paused_reason/paused_at/last_error/时间戳；2026-09-10 additive 加 `probe_token`（TEXT / VARCHAR(64)，nullable，无回填）——半开探测认领的 CAS 键；同日再加 `probe_claimed_at`（TEXT / DATETIME(6)，nullable，与 `probe_token` 同写同清）——认领时刻，崩溃窗口兜底只把「认领之后才开始」的 run 当作可能的认领者（PR #394 review I1）。存量表由 `auto_migrate` 的 ADD COLUMN 路径补上。

## 2026-07-09 — agent_slots (per-agent LLM slot overrides)

New table ``agent_slots`` (registered right after ``user_slots``), mirroring
``user_slots`` column-for-column but keyed by ``agent_id``. A row here overrides
the owner's ``user_slots`` for that slot on runs of THIS agent only; absence =
inherit the user default. Both ``agent`` and ``helper_llm`` slots may be
overridden (helper follows its agent). Identical column vocabulary is deliberate:
``resolver._apply_agent_overrides`` overlays a row onto ``by_slot_name`` and the
existing card-lookup / self-heal / driver-dispatch consumes it unchanged. Unique
index ``(agent_id, slot_name)`` + ``(agent_id)``. Additive migration only.

## 2026-06-10 — user_slots.params_json column

`user_slots` gained a nullable `params_json` (TEXT/MEDIUMTEXT) column: one
extensible JSON object for framework-neutral per-slot params (currently
thinking + reasoning_effort; future per-slot knobs reuse it without another
migration). NULL = all params auto. Purely additive — auto_migrate() adds
it on next startup of every process.

## 2026-06-09 — embedding subsystem removed → ORPHANED ZOMBIE data (cleanup DEFERRED)

The unified-memory refactor dropped the entire embedding/RAG subsystem
(retrieval is now BM25 + grep, see [[record]] "No embeddings anywhere"). This
registry therefore NO LONGER declares:

- **whole tables**: `embeddings_store`, `chat_message_embeddings`,
  `instance_rag_store`
- **columns on shared/active tables**: `narratives.routing_embedding` /
  `embedding_updated_at` / `events_since_last_embedding_update`,
  `events.event_embedding` / `embedding_text`, `*.capability_embedding`, etc.

`auto_migrate` is **additive-only** (it iterates the REGISTRY and does
CREATE/ADD/INDEX IF NOT EXISTS; it never enumerates the live DB to DROP
extras — binding rule #6). So on every **already-deployed** database (cloud
MySQL + local `run.sh`/DMG SQLite) those tables and columns **remain in place
as orphaned zombie data**. This is intentional and safe:

- no live code path reads/writes them (verified: zero embedding-table refs +
  zero deleted-module imports across `src/` + `backend/`);
- every dropped column on an active table was nullable or carried a DEFAULT
  (the only NOT NULL one, `events_since_last_embedding_update`, had
  `default=0`), so new code's INSERTs (which omit them) are never rejected.

Cost: a little disk, zero functional impact. **DEFERRED (buffering):** a future
explicit, idempotent cleanup migration (`mNNNN`: `DROP TABLE IF EXISTS
embeddings_store / chat_message_embeddings / instance_rag_store`, drop the dead
columns) should run through the versioned `migrations/` ledger — NOT through
`auto_migrate` — so the destructive step is audited, run-once, and Owner-
authorized (rules #6/#12). Not done in this release on purpose.

## 2026-06-09 — schema_migrations ledger table

Added the `schema_migrations` TableDef (migration_id PK / applied_at /
app_version / notes) — the run-once ledger for the versioned data-migration
runner (see migrations/ [[__init__]]). `auto_migrate` creates it like any other
table, so the runner (which fires right after auto_migrate at startup) can
read/write it.

## 2026-06-08 — source_ref column + MEMORY_KINDS

The `memory_<kind>` table definition (`_memory_kind_table`) gained an additive `source_ref` column (TEXT/JSON) for the projection pointer. `MEMORY_KINDS` enumerates the memory kinds (event/narrative/chat/entity/bus/job/observation) used by account-deletion and bundle paths. `instance_social_entities` TableDef is KEPT (bundle round-trip builds a fresh DB via auto_migrate and still needs it), but no live code path writes it any more — entities live in `memory_entity` (see [[social_network_repository]]).

## 2026-06-08 — user_settings table (analytics opt-out)

New table `user_settings` — per-user flat-column preferences. First consumer:
`analytics_opt_out` (TINYINT(1), default 0). A missing row means "not opted
out" — read path in `UserSettingsRepository.is_analytics_opted_out` returns
`False` when no row exists. Insert-or-update pattern in
`set_analytics_opt_out`: single `get_one` + branch on existence; `updated_at`
is not updated in-band because `db.update` uses parameterized placeholders
(the raw SQL expression `(datetime('now'))` would be stored as literal text).
New columns can be added via the registry as new preferences appear —
`auto_migrate` is additive.

## 2026-06-17 — user_slots.agent_framework column

`user_slots` 新增 nullable `agent_framework`（TEXT/VARCHAR(32)，DDL 默认
`'claude_code'`）。只在 `slot_name='agent'` 那一行有意义，驱动
`step_3_agent_loop` 的 SDK 分发：`"claude_code"` → ClaudeAgentSDK，
`"codex_cli"` → CodexSDK。带默认值是为了让已存在的旧行无需单独 backfill 就向后兼容
——resolver 同样把 null 当作 claude_code 处理。纯 additive，`auto_migrate` 下次启动
自动 `ALTER TABLE ADD COLUMN`。

## 2026-06-11 — invite_codes table marked retired (data kept)

Table definition stays so existing rows survive: they hold the only old-user-id -> email mapping needed by scripts/data_migrations/migrate_users_to_netmind.py. No code writes the table anymore; safe to drop after migration completes.

## 2026-06-10 — user_slots.params_json column

`user_slots` gained a nullable `params_json` (TEXT/MEDIUMTEXT) column: one
extensible JSON object for framework-neutral per-slot params (currently
thinking + reasoning_effort; future per-slot knobs reuse it without another
migration). NULL = all params auto. Purely additive — auto_migrate() adds
it on next startup of every process.

## 2026-06-09 — embedding subsystem removed → ORPHANED ZOMBIE data (cleanup DEFERRED)

The unified-memory refactor dropped the entire embedding/RAG subsystem
(retrieval is now BM25 + grep, see [[record]] "No embeddings anywhere"). This
registry therefore NO LONGER declares:

- **whole tables**: `embeddings_store`, `chat_message_embeddings`,
  `instance_rag_store`
- **columns on shared/active tables**: `narratives.routing_embedding` /
  `embedding_updated_at` / `events_since_last_embedding_update`,
  `events.event_embedding` / `embedding_text`, `*.capability_embedding`, etc.

`auto_migrate` is **additive-only** (it iterates the REGISTRY and does
CREATE/ADD/INDEX IF NOT EXISTS; it never enumerates the live DB to DROP
extras — binding rule #6). So on every **already-deployed** database (cloud
MySQL + local `run.sh`/DMG SQLite) those tables and columns **remain in place
as orphaned zombie data**. This is intentional and safe:

- no live code path reads/writes them (verified: zero embedding-table refs +
  zero deleted-module imports across `src/` + `backend/`);
- every dropped column on an active table was nullable or carried a DEFAULT
  (the only NOT NULL one, `events_since_last_embedding_update`, had
  `default=0`), so new code's INSERTs (which omit them) are never rejected.

Cost: a little disk, zero functional impact. **DEFERRED (buffering):** a future
explicit, idempotent cleanup migration (`mNNNN`: `DROP TABLE IF EXISTS
embeddings_store / chat_message_embeddings / instance_rag_store`, drop the dead
columns) should run through the versioned `migrations/` ledger — NOT through
`auto_migrate` — so the destructive step is audited, run-once, and Owner-
authorized (rules #6/#12). Not done in this release on purpose.

## 2026-06-09 — schema_migrations ledger table

Added the `schema_migrations` TableDef (migration_id PK / applied_at /
app_version / notes) — the run-once ledger for the versioned data-migration
runner (see migrations/ [[__init__]]). `auto_migrate` creates it like any other
table, so the runner (which fires right after auto_migrate at startup) can
read/write it.

## 2026-06-08 — source_ref column + MEMORY_KINDS

The `memory_<kind>` table definition (`_memory_kind_table`) gained an additive `source_ref` column (TEXT/JSON) for the projection pointer. `MEMORY_KINDS` enumerates the memory kinds (event/narrative/chat/entity/bus/job/observation) used by account-deletion and bundle paths. `instance_social_entities` TableDef is KEPT (bundle round-trip builds a fresh DB via auto_migrate and still needs it), but no live code path writes it any more — entities live in `memory_entity` (see [[social_network_repository]]).

## 2026-06-08 — user_settings table (analytics opt-out)

New table `user_settings` — per-user flat-column preferences. First consumer:
`analytics_opt_out` (TINYINT(1), default 0). A missing row means "not opted
out" — read path in `UserSettingsRepository.is_analytics_opted_out` returns
`False` when no row exists. Insert-or-update pattern in
`set_analytics_opt_out`: single `get_one` + branch on existence; `updated_at`
is not updated in-band because `db.update` uses parameterized placeholders
(the raw SQL expression `(datetime('now'))` would be stored as literal text).
New columns can be added via the registry as new preferences appear —
`auto_migrate` is additive.

## 2026-05-27 — instance_jobs.created_at/updated_at NOT NULL + DEFAULT

`instance_jobs` table had `created_at` / `updated_at` columns with no
constraint and no DEFAULT. Some code paths INSERTed rows leaving
those columns NULL, which then crashed `job_trigger`'s pydantic
`JobModel` validation (see [[job_repository]] companion fix). Added
`nullable=False` + `default="(datetime('now'))"` so future INSERTs
can't recreate the bug. `auto_migrate` is additive-only — existing
NULL rows in already-deployed sqlite DBs are handled at the read
boundary by `_row_to_entity` defensively coercing None to
`datetime.now()`.

## 2026-05-14 — artifact pointer model

## 2026-07-21 — instance_artifact_versions no longer registered

The dead `instance_artifact_versions` TableDef was removed from the registry.
Safe unilaterally: auto_migrate never drops, so existing databases keep the
table and rows untouched (hand-migration of old saved HTML still possible);
fresh databases simply stop provisioning a table no code has read or written
since 2026-05-14. `instance_artifacts.latest_version` stays registered — a
column removal only matters together with the destructive DROP migration,
which remains one Owner-gated batch (铁律 #6); see the cleanup TODO.

`instance_artifacts` gains two pointer-model columns: `file_path` (entry file
relative to `base_working_path`, nullable so auto_migrate adds it to existing
DBs without a backfill) and `size_bytes` (recursive size of the artifact root
directory, `NOT NULL DEFAULT 0`).

`instance_artifacts.latest_version` and the whole `instance_artifact_versions`
table are now **DEPRECATED** — versioning was dropped with the pointer model.
Both are kept registered (so auto_migrate keeps provisioning them) purely so
colleagues with old saved HTML can hand-migrate from the old rows. No code reads
or writes them. Cleanup is tracked (author-local todo).

## 2026-05-14 addition — invite_codes

New table `invite_codes` — backs the cloud-mode registration gate, replacing
the single global `INVITE_CODE` env var (deleted from `backend/auth.py`).

One row = one unique, single-use code issued to one email. `code` carries a
unique index; `email` / `status` indexes drive idempotent re-requests (same
email → resend existing issued code) and the Mode-B auto-issue cap count
(`status IN (issued, used)` < cap). status flow: `issued → used` (consumed
atomically by `/api/auth/register`), `waitlisted → issued` (admin promote
when the cap is hit), `→ revoked` (admin kill). `email_sent` records whether
the SMTP send actually succeeded so a failed send is visible/re-sendable in
the admin list without blocking `/api/invite/request`.

Purely additive — `auto_migrate` creates it on next startup. (Design log
is author-local, untracked.)

## 2026-05-13 addition — Agent Runtime Lifecycle (Phase C)

`events` 表加 7 个 Phase-C 字段：`state` / `started_at` / `last_event_at`
/ `finished_at` / `tool_call_count` / `current_stage` / `error_message`。
**`state` 的 DDL 默认值是 `completed`**——这是给已存在的旧 events 行的兜底，
让它们不被启动期 reconcile 误判成 stale `running`。

新增 `idx_events_state` + `idx_events_agent_state` 两个索引——前者给
reconcile 扫 stale 行用，后者给 `/api/auth/agents` list 加 active_run
字段的 N+1 SELECT 用（实际 endpoint 用了 IN-列表合并成单个 SELECT，但
索引仍是底层优化）。

新增 `event_stream` 表（编号 30.）——per stream-chunk 副表，跟 `events`
1:N 关联。每段 thinking、每个 tool_call、每个 tool_output 一行。
`(event_id, seq)` unique 复合索引让重连时的 replay 按 seq ASC 一次扫
出全部。**永不清**——audit + 历史回看。

数据量估算（Xiong-style 13 min run）：thinking 段约 50 行 + tool 约 80
行（call + output 各 41）+ progress / text_delta 若干 ≈ 200 行/run。
13 万 run/年 ≈ 2600 万行，~25GB——MySQL 无压力。

## 2026-05-13 addition — Provider Unification (Phase 0)

`user_providers` gains four nullable columns — `driver_type`, `owner_user_id`,
`billing_policy`, `auth_ref` — plus two indexes (`idx_up_driver_type`,
`idx_up_owner`). `user_slots` gains `last_auto_repaired_at` (nullable) used
as the 24h debounce timestamp for the reverse-validation self-heal path.

New table `user_notifications` (29.) — minimal kind+payload+severity row
written by the resolver when it auto-repairs a broken slot. Indexed on
`(user_id, read_at)` for the "unread count" UI query.

Driver inference (`derive_driver_type`) and one-shot backfill live in
`src/narranexus/platform/agent_framework/providers/driver/backfill.py`. New
deploys get `driver_type` written at `add_provider` time; pre-existing rows
get backfilled on the next backend boot via `auto_migrate` → `backfill_*`
chain in `db_factory.get_db_client`. Both column-add and backfill are
idempotent so re-running causes no drift.

All new columns are nullable on purpose — older `bash run.sh` / desktop
DMG users upgrade with zero schema drama: `auto_migrate` runs the
ALTERs, the backfill fills the values, business code never sees a
null after the first boot. Old columns (`source`, `auth_type`,
`linked_group`, `prefer_system_override`) are untouched.

## 2026-05-09 hardening — I7 idx_artifact_agent_id added

`instance_artifacts` now has a third index `idx_artifact_agent_id` on `["agent_id"]`.
`total_bytes_for_agent` joins `instance_artifact_versions` to `instance_artifacts` on
`artifact_id` and filters by `agent_id`. Without an `agent_id` index the planner may
scan the full `instance_artifacts` table when an agent has many artifacts. The two
existing composite indexes (`idx_artifact_agent_session`, `idx_artifact_agent_pinned`)
cover query patterns with two conditions; the new single-column index covers the quota
aggregation join path.

## 2026-04-28 addition — chat_message_embeddings folded in

Registered `chat_message_embeddings` here alongside the other
`_register(TableDef(...))` calls. This was the last table in the
codebase still living under the legacy "one create script per table"
model in `utils/database_table_management/`. The script was orphaned —
nothing in the codebase imported it, so every fresh local DB was
missing the table, every ChatModule hook was failing silently with
`no such table: chat_message_embeddings`, and `ChatModule` was
burning embedding API calls every turn for nothing (Bug #1).

The orphan script `create_chat_message_embeddings_table.py` is gone;
new deployments build the table via `auto_migrate()` like every other
table. The whole `utils/database_table_management/` folder no longer
exists.

Reader side stays empty for now: nothing reads from the table yet —
the intended Part B retrieval surface for ChatModule history was
never wired up. Letting the writer succeed silently lets embeddings
accumulate for whatever surface gets built later.


Single source of truth for every database table — define columns once, run on both SQLite and MySQL, migrate automatically.

## Why it exists

Before this file, table schemas lived only as raw `CREATE TABLE` SQL strings in individual `create_*_table.py` scripts, one set per dialect. Columns could drift between environments and there was no programmatic way to detect what needed migrating. `schema_registry.py` centralizes every column and index definition in Python dataclasses. The `auto_migrate` path reads `TABLES` at startup and issues `ALTER TABLE ADD COLUMN` for any column present in the registry but absent from the live database. The registry also feeds `_get_unique_cols_for_table()` in `database.py` when it needs to build `ON CONFLICT(...)` targets for SQLite upsert statements.

## Upstream / Downstream

**Consumed by:**
- `database.py` — `_get_unique_cols_for_table()` reads `TABLES` to resolve conflict columns for `ON DUPLICATE KEY UPDATE` translation.
- `database_table_management/auto_migrate.py` and the `create_*` scripts — iterate `TABLES` to create missing tables and add missing columns.
- Tests and tooling that call `get_registered_tables()` — the public accessor returns `list(TABLES.values())` so callers don't need to import the private `TABLES` dict directly.

**Depends on:** nothing inside the application. Pure-Python dataclasses; the only runtime import is `loguru`.

## Design decisions

**Dual-type columns (`sqlite_type` / `mysql_type`).** Each `Column` carries both `sqlite_type` (TEXT, INTEGER, REAL, BLOB) and `mysql_type` (VARCHAR(64), MEDIUMTEXT, TINYINT(1), etc.). DDL generators pick the appropriate field for their target dialect. This makes the registry the single place to update a type mapping.

**Append-only migration contract.** `auto_migrate` only adds columns — it never drops, renames, or narrows them. Removing a column from the registry has zero effect on the live database. This is intentional: destructive schema changes require a manual DBA operation. Any attempt to auto-drop columns would be a violation of the project's "no dangerous DB mutations" rule.

**`_register()` at module load time.** Table definitions are registered via `_register(table_def)` at the module's top level, not inside a function. Importing this module is enough to populate `TABLES`. Test fixtures that need extra tables can call `_register` after import.

**No ORM, no query builders.** The registry owns the database shape. Pydantic models live separately in `schema/`. `AsyncDatabaseClient` methods take plain Python dicts, not registry objects.

**`TableDef.primary_key` list for composite PKs.** Most tables have a single auto-increment `id` column with `primary_key=True` on the `Column`. Tables with composite primary keys (e.g., `bus_channel_members`) use the `TableDef.primary_key` list field instead. DDL generators must check both.

## Gotchas

**Adding a column does not migrate existing databases automatically.** `auto_migrate` must be explicitly run (`make db-sync`). Forgetting to run it after pulling new code produces `sqlite3.OperationalError: table X has no column named Y` at runtime, which looks like a code bug.

**SQLite `default` values use SQLite syntax.** The `default` field stores a SQLite expression — e.g., `"(datetime('now'))"` not `"CURRENT_TIMESTAMP(6)"`. MySQL DDL generators must translate these. Copying a default value from a MySQL script verbatim will cause SQLite to reject the `CREATE TABLE`.

**JSON columns are TEXT in SQLite.** Columns with `mysql_type = "JSON"` carry `sqlite_type = "TEXT"`. SQLite's `json_extract` works on TEXT, but MySQL's JSON type enforcement does not apply. Malformed JSON written from application code will be stored without error.

**Upserts need the table registered.** `database.py` falls back to `[table_name]` as the conflict target if the table is not in `TABLES`. An unregistered table that receives an upsert call will silently insert duplicates instead of updating.

**New-contributor trap.** Registering a table here is necessary but not sufficient for a first-time install. The corresponding `create_*_table.py` script must also exist, because `auto_migrate` only adds columns to tables that already exist. A freshly cloned repo with no tables gets nothing from the registry alone.

## 2026-04-21 · v2 时区协议字段

`instance_jobs` 表新增 4 列：`next_run_at_local` / `next_run_tz` / `last_run_at_local` / `last_run_tz`（全部 TEXT/VARCHAR, nullable）。语义：前端不感知 UTC,所有时间以 "local + tz" 配对流动(job 时区重设计 2026-04-21)。

这些列是 additive 变更，`auto_migrate` 启动时自动 `ALTER TABLE ADD COLUMN` 即可。**不改**原 `next_run_time` / `last_run_time` 列名或类型（它们在新协议下专职承载 UTC，对 LLM 不可见）。

## 2026-05-08 · Agent Artifact Tabs — instance_artifacts + instance_artifact_versions

Two new tables registered as part of the Agent Artifact Tabs feature
(2026-05-08).

**`instance_artifacts`** — one row per artifact emitted by the agent (chart,
csv, markdown, html app, png/jpeg/pdf, etc.). Text primary key `artifact_id`
(prefix `art_` + 8 random chars). Tracks `kind`, `title`, `description`,
`pinned` flag, and `latest_version` counter. `agent_id` and `user_id` are
`VARCHAR(128)` (aligned with `instance_jobs`, `module_instances` and other
module-owned tables — the wider width prevents MySQL truncation for IDs that
can exceed 64 chars in some generator configurations). Indexed on
`(agent_id, session_id)` and `(agent_id, pinned)` for the two common query
patterns: "all artifacts in this session" and "pinned artifacts for this agent".

**`instance_artifact_versions`** — append-only version log. Each row stores the
`file_path` to the artifact file on disk and `size_bytes`. The composite unique
index on `(artifact_id, version)` enforces immutability: a given version of a
given artifact cannot be overwritten. The `latest_version` counter in
`instance_artifacts` is bumped on each new version write.

Both tables are purely additive and take effect on next `auto_migrate()` call
(i.e., next app startup).

## 2026-05-08-r2 · original_session_id column added to instance_artifacts

Added a nullable `original_session_id TEXT/VARCHAR(64)` column to
`instance_artifacts`. This stores the `session_id` at the moment the artifact
is pinned, so that `set_pinned(False)` can restore it instead of leaving the
artifact orphaned with `session_id=NULL`. Purely additive — existing rows get
`NULL` (no session to restore; the route layer surfaces a warning per review
Important #1).

## 2026-08-11 — `team_bulletin_entries`

纯新增一张表（铁律 #6），`auto_migrate` 幂等建它，不动任何既有行。

两个列形状是有意的，并有测试钉住：

- **`source` 与 `author_id` 分开**。`source` 决定**规则**（谁能删、是否占预算、怎么渲染），
  `author_id` 决定**显示**。合成一列，权限判断就得去解析字符串前缀。
- **`author_id` 可空**，因为自动总结有 source 没 author。设成 NOT NULL 就得造一个
  `"system"` 哨兵，然后每个「谁写的」路径都要用字符串比较把它排除掉。
- **`watermark_at` 是专用列**，只在总结行有值。第一版把它塞进 `author_id`——
  那正是上面那条自己批评的一列两义，提交前改掉。

## 2026-08-12 — `bus_messages.routed_by`(可空,additive)

记录一条消息的 `mentions` 是谁写的。语义见 [[schemas]]。

**为什么不复用 `msg_type`**:`send_message` 会把带附件的消息自动改写成
`"multimodal"`,而"无人被 @"是**正交**的另一个事实,两者塞进同一列会互相覆盖。
`multimodal` 目前没有消费方,但重载一个字段表达两件事迟早出事。加一个可空列是本项目
的常规机制,`auto_migrate` 幂等处理,不触发铁律 #6(它禁的是收窄类型和破坏性迁移)。

## 2026-08-14 — `bus_messages.event_id` 的口径扩了(更正 07-31)

07-31 那节写的「stamped by the trigger's team branch at post time」现在只对一半:
agent 自己调 `bus_send_message` / `bus_send_to_agent` 发的行也盖(身份头),DM 频道的行
同样带 id。所以它**不是**「平台代发」的标记,`event_id IS NOT NULL` 当那个用会多算。
完整口径与三种 NULL 情形见 [[schemas]] 的 08-14 节;列注释本身已同批改过。

## 2026-08-12 — `bus_messages.segments`

纯新增可空列（铁律 #6），JSON 文本。保存独白/回复边界，`content` 保持不变——
后者是所有文本消费者读的东西，一个渲染需求不该改写它。

## 2026-08-18 — `instance_artifacts.content_hash` + 新表 `instance_artifact_events`

**content_hash(可空,additive)**:注册时对 entry 文件算 sha256 存这里。heal 用它给
候选**验明正身**——「改名但内容未动」从按扩展名猜升级成确定性认领。可空的两层含义:
存量行 NULL(heal 跳过 hash 层),以及哈希失败绝不阻塞注册(best-effort 契约)。类型
与 `team_files.content_hash` 同款(TEXT/VARCHAR(64)),将来 7′ 存档层直接复用同一指纹。

**instance_artifact_events(跨进程 outbox)**:artifact_changed 事件的staging 表。
为什么需要它:register_artifact 跑在 MCP 工具进程,而通往前端 WS 的 Broadcaster 在
backend 进程——写入方把自包含 payload 落成一行,BackgroundRun 在每个 tool-output
事件后 drain 本 agent 的未消费行并经 `self.emit()` 重发(录制+广播一次拿齐)。
run 外staging 的行(如 HTTP 删除)会迟到 drain——刻意如此:前端 updated_at 单调守卫
中和迟到事件,打开时全量拉是自愈地板,所以无 TTL 无跨进程锁。`consumed_at` 不删行,
近期尾部兼作投递审计。设计出处:spec 2026-08-18-artifact-events-inventory-pointer §3。

> **2026-08-20 平台默认框架变更**: 无显式选择时的默认 agent framework 由 `claude_code` 改为 `nexus_power`（免费/默认用户跑自研 NexusPower loop；模型不变）。本文件相关默认/兜底串已随之更新。

> **2026-08-21 steer_inbox**: 新增 `steer_inbox`(21c)——运行中插话注入的统一 inbox。**理由=解耦+per-run 游标,非持久化**(team 消息本在 bus_messages;但单聊插话不进 bus,统一 inbox 让 feeder 只 drain 一处,同 artifact_events outbox)。`id` 自增=到达序+消费游标;`(run_id, msg_id)` 唯一(重投递最多注入一次);`consumed_at` 是 bus (agent,channel) 游标给不了的 per-run 游标;`(run_id, consumed_at)` 是 pull-unconsumed 访问路径。owner=[[steer_inbox_repository.py]],schema=[[steer_schema.py]]。

## 2026-08-24(补)— steer_inbox 加 idx_steer_inbox_created

`steer_inbox` 加 `Index("idx_steer_inbox_created", ["created_at"])`:支撑 `cleanup_older_than_days` 的两臂 DELETE
(都过滤 `created_at`),否则 MySQL 上全表扫。走 registry(`auto_migrate` 幂等加索引,非手写 ALTER)。见
[[message_bus_trigger.py]] 补10 / [[steer_inbox_repository.py]] 补4。

## 2026-09-04 · generic channel credentials (batch 4b)

`channel_credentials` — the generic channel credential table (channel, agent_id, enabled, external_id, public_json, encrypted secret_json; unique (channel, agent_id) and (channel, external_id)). The six per-channel tables stay (never dropped) through the dual-write phase.

## 2026-09-04 · webhook transport (batch 4c)

`channel_webhook_events` — the webhook transport inbox (channel, agent_id, payload_json, received_at, claimed_at).

## 2026-09-04 · generic store as the source of truth (batch 4d.1)

`channel_credentials.version` for the store's optimistic patches. `channel_telegram_credentials` / `channel_slack_credentials` / `channel_discord_credentials` / `channel_wechat_credentials` are retired (no longer read or written) and kept, never dropped (rule #6).

## 2026-09-04 · `channel_credentials` is the only live credential table (batch 4d.2)

The six per-channel credential tables stay registered (rule #6: never dropped; `credential_legacy` copies them in once) but nothing writes them any more. Comment on the generic table updated accordingly.

## 2026-09-04 · `agent_capabilities` (batch 5c)

The owner's per-agent capability switches, unique per (agent_id, capability); no row = the default rule. Written by the capabilities route, read by the loader.
