---
code_file: backend/routes/auth.py
last_verified: 2026-10-28
stub: false
---

## 2026-10-28（PR #419 review I-8）— funnel 三个限额改走 `env_limit`

`FUNNEL_REPORT_PER_EMAIL_LIMIT` / `_PER_IP_LIMIT` / `_GLOBAL_LIMIT` 从写死常量改为
`env_limit("NARRANEXUS_FUNNEL_REPORT_*", DEFAULT_…)`，与同文件 signup / netmind-login
五个桶同一写法：线下活动或实时滥用时运维可只改 env 调限额，不动代码；env 里的空值 /
非整数 / <1 回落默认，不会让 backend 启动期崩。默认值不变（10 / 30 / 2000），`.env.example`
加了三行注释示例。`WINDOW_SECONDS`(60) 不是限额，仍写死。

## 2026-10-28（PR #419 review M-4）— funnel 限流数字改为具名常量

`FUNNEL_REPORT_PER_EMAIL_LIMIT`(10) / `FUNNEL_REPORT_PER_IP_LIMIT`(30) /
`FUNNEL_REPORT_GLOBAL_LIMIT`(2000) / `FUNNEL_REPORT_WINDOW_SECONDS`(60)。值不变；
`test_auth_funnel_observability.py` 的 fixture 改用这些常量重建桶（原来手抄了过时的
global=120），测试跑的就是线上尺寸。丢弃汇总日志的 60s 节拍是另一回事，未并入。

## 2026-10-28 — `/agents` 预览只给自己的 agent；funnel 全局桶改为兜底

- 侧栏"最近一条回复"预览原来对列表里的**公开 agent**也查：任何人能看到别人公开 agent
  最近回复的前 200 字。现在只对 `created_by == 调用者` 的 agent 查预览。
- funnel-report 的全局桶 120/min 四个 IP 就能把正常用户的上报挤掉；按 IP 的 30/min
  才是真正的限制，全局改成 2000/min 的兜底。
## 2026-10-28 — signup / send-code 加 per-IP 桶 + 一次性邮箱拒收

事件：同一 IP 自助注册两个号后对 API fuzz 4 小时。审计发现 send-code 只有
「每 email 每 60s 一次」——每次换一个新地址就无上限，等于把我们的 NetMind 发信
通道借给任何人去轰炸任意邮箱；signup 也只有 per-email 10/10min。

- **per-IP 桶**（`client_ip`，与 funnel-report 同一个 edge-appended XFF 取值；同一个
  `SlidingWindowRateLimiter`）：send-code 10/min + 60/h，signup 尝试 60/h + 建成 20/h，netmind-login 120/h
  （按办公室 / 线下活动共用出口的 NAT 定的默认值）。默认值是模块常量 `DEFAULT_SIGNUP_*`，
  生效值 `SIGNUP_CODE_PER_IP_PER_MINUTE` / `SIGNUP_CODE_PER_IP_PER_HOUR` /
  `SIGNUP_ATTEMPTS_PER_IP_PER_HOUR` / `SIGNUP_PER_IP_PER_HOUR` /
  `NETMIND_LOGIN_PER_IP_PER_HOUR` 经 `env_limit` 从 `NARRANEXUS_SIGNUP_*` /
  `NARRANEXUS_NETMIND_LOGIN_PER_IP_PER_HOUR` 读（已进 .env.example，并提示线下活动前调高）。
  **import 期读取**：改 env 后必须重建 backend 容器才生效。429 一律带 `Retry-After`
  （per-email 的 429 也补上了）。per-email 限额保留。
- **signup 有两个 per-IP 桶**：
  - **建成桶** `_signup_ip_limiter`（20/h）只记「真建成账号」的请求：先 `allow` 预占
    （并发请求不能同时溜过一次检查），任何没建成账号的出口——per-email 429、验证码错/
    已注册（`RegistrationError` 400）、上游 502、客户端断开取消——在 `finally` 里
    `refund`。目的：失败不烧共享办公室的创建额度；验证码爆破由 per-email 10/10min 兜。
  - **尝试桶** `_signup_attempt_ip_limiter`（60/h，`NARRANEXUS_SIGNUP_ATTEMPTS_PER_IP_PER_HOUR`；与 send-code 的 per-IP 小时桶同额——
    每次真实注册都要先拿一次验证码，60/h 仍是建成桶的 3 倍，够错码重试）
    对每个请求全额计费、**永不退还**。PR #420 review I-1：只有建成桶时，轮换 email + 错码
    的请求全部走失败路径、名额全部退回，单 IP 可以无限打上游 register（我们的出口 IP 成了
    放大器），per-email key 也无封顶地堆积。尝试桶就是这条路由真正的 per-IP 上限。
    建成桶的语义（失败不烧共享 NAT 的创建额度）保持不变。
  - **陷阱**：`finally` 里只能退建成桶；把尝试桶也放进退还分支等于没加（测试
    `test_failed_signups_are_capped_by_the_attempt_bucket` 会红）。
- **求值顺序即不变量**：send-code = 拒收域 → per-IP 分钟 → per-IP 小时 → per-email →
  上游；signup = 拒收域 → 密码策略 → per-IP 尝试桶 → per-IP 建成桶（预占）→ per-email →
  上游（失败只退建成桶）。per-IP 在 per-email 前：可信有界的 key 给 caller-chosen email
  key 的分配封顶（同 funnel-report R4；signup 上由永不退还的尝试桶保证）。
  `_enforce_ip_buckets` 短路：分钟桶拒绝不花小时桶；反之不成立——小时桶拒绝时分钟桶
  已经扣过。signup 的尝试桶**必须排在建成桶前面**：尝试桶拒绝时建成桶还没预占；反过来
  放，建成桶先预占、尝试桶在 `try` 之前抛出，`finally` 的退还永远到不了，这个名额就被
  永久吃掉（测试 `test_attempt_bucket_rejection_spends_no_creation_hit_and_no_email_key`）。
  密码策略在 per-IP 前：弱密码拒绝不花上游，不该把共享 NAT 的一小时额度烧掉。
- **netmind-login 的 per-IP 桶**（PR #420 review M-4）：这条路由同样未认证，且每个请求
  （无效 token 也一样）都向 NetMind auth API 发一次 `verify_token`——与 signup 同类的
  「未认证路由 → 上游放大」。`_netmind_login_ip_limiter` 120/h，全额计费、不退还，
  排在 `is_power_login_enabled()` 的 404 之后（本地未开 Power login 不计桶，与 signup
  路由一致）、`verify_token` 之前（被拒请求不花上游）。宽松：真实用户一天只登录几次，
  一屋子人共用 NAT 也装得下。429 日志走 `[login-funnel]` 标签（`_enforce_ip_buckets`
  的 `funnel` 参数），和该路由其余 funnel 行在同一个 grep 里。测试仓根
  `tests/conftest.py` 的 autouse fixture 每个用例重置这个单例（多个测试模块经此路由登录，
  且 TestClient 的 peer 都是 `testclient`）。
- **一次性邮箱**：`_email_policy.is_blocked_email`（见其 mirror），400 + 中性文案
  `_BLOCKED_EMAIL_DETAIL`，排在所有桶之前：拒收域请求不花任何 per-IP 桶、不分配
  email key、不调上游。因此黑名单可被免费无限枚举——这是有意的（PR #420 review M-1）：
  条目都是公开的知名一次性邮箱域，枚举出来不是情报；挪到 per-IP 桶之后反而让打错域名
  的真实用户白花一次额度。中性文案只是不主动提示，不是防护。
- **登录期免费额度门（绕过路径的兜底）**：`NETMIND_AUTH_API_URL` 经 `/config.js`
  公开，用户可以直接在 NetMind 注册、完全绕开上面两条路由；而 $10 钱包与引导
  Agent 是在 **netmind_login** 里发的。`_free_credit_withheld(user_id, email)` 对
  NetMind 校验过的 email 做同一个 `is_blocked_email`，命中则 warning 日志（只记域名——
  比 signup funnel 记完整 email 更严，因为它对每一次登录生效，不只是被拒的注册尝试）、
  `_provision_providers(..., free_tier=False)` 跳过钱包（连钱包服务都不调）、不调度
  引导 Agent、响应 `guide_agent_provisioning=False`（coachmark 同门）。用户照常登录，
  自己的 NetMind 卡照常注册。本地 `login` / `create_user` 无 email、无钱包，不受影响。
- **per-IP 限额是纵深防御，不是闭环**：NetMind 直连注册存在，真正的「不给薅」靠的是
  登录期免费额度门；per-IP 桶只是抬高经我们路由批量注册 / 轰炸发信的成本。
- 测试：`tests/backend/test_signup_abuse_limits.py`（含失败不扣建成桶、失败照扣尝试桶并在
  上限处 429、尝试桶拒绝不预占建成桶也不分配 email key、拒收域不扣 IP 桶、
  netmind-login 第 N+1 次 429 且另一 IP 照常、Power login 关闭时 404 不计桶、
  登录期真跑 free-tier provisioner + mock 钱包客户端验证不调钱包）；
  `test_signup_routes.py` 的 fixture 同步重置 signup / send-code 的四个 per-IP limiter（模块单例会跨用例泄漏）。

## 2026-09-10（PR #394 第二轮 review I-4）— 活性规则单一导入路径

`run_is_live` 改为从 `narranexus.platform.utils.run_liveness` 导入（原来经 `background_run`
转手）；`_run_is_live` 别名与 agents 列表 active_run 过滤行为不变。下方 2026-06-10 一节里「搬到
background_run.py」是历史。

## 2026-09-08 — 空白建 agent 的占位名用 `PLACEHOLDER_AGENT_NAME`

与 bootstrap 模板共用同一个常量，问候渲染据此判断「还没起名」。

## 2026-09-07 — 删除 agent：多一遍与发行版无关的 legacy 凭据清扫（14a）

原有的 14 号步骤是「按 module_registry 遍历每个 `ChannelModuleBase` 子类，各自
`cleanup_for_agent`」。这条路径只覆盖**本发行版装了**的渠道；被排除的渠道（两个示例发行
版排除了全部六个）没有模块可走，它退役前的 `lark_credentials` /
`channel_*_credentials` 行——base64 bot token 和 app secret——就永远留在库里，包括用户删掉
agent 之后。

14a 补的就是这一遍：`credential_legacy.purge_legacy_for_agent(db, agent_id)`，按
`agent_id` 扫 `LEGACY_TABLES`（曾经存过渠道密钥的表的唯一真值表），不需要描述符、不需要
插件。与 14 里的每渠道清理重叠无害（第二条 DELETE 匹配不到行）；按表尽力而为，从来没有
这张表的安装不会让删除失败。

## 2026-08-20 — 前端用的 bootstrap_active 是宽松版(只 isfile,无阈值)

`/api/auth/agents`(list)与 `PUT /agents/{id}` 响应里填的 `AgentInfo.bootstrap_active` 都是
`os.path.isfile(Bootstrap.md)` 一句、**不含 event_count 阈值**——因为 list 接口担不起每 agent 一次
COUNT。它 gate 前端那颗静态问候气泡(ChatPanel `showBootstrapGreeting`),list 里还据它决定是否下发
`bootstrap_greeting`。这与后端两个问候写入方共用的 [[../../../src/narranexus/platform/bootstrap/lifecycle]]
`.is_bootstrap_active`(含阈值)是**两条规则**,只在「越阈值但 Bootstrap.md 未被 auto-delete」的窄
窗口分叉(前端显示气泡、写入方拒绝落库,刷新后消失)。改「什么算引导期」时两处一起看;源码已加注释
指回 lifecycle,可 grep。统一需先解 list 接口 N+1(记 `reference/self_notebook/todo/`)。

## 2026-08-19 (九改·更正) — 不再有 `extra_updates` 这个参数

四改那条只更正了 import 路径，把签名留在了旧样子。现在传的是**具名参数**：

```python
result = await apply_agent_profile_change(
    db_client, agent_id,
    new_name=..., new_description=...,
    is_public=... ,          # None 表示"没传"；False 是值
)
```

`extra_updates` 已经不是参数，只是事务**内部**拼出来的局部 dict。改成具名的理由
见 [[profile_write]] 八改：开放 dict 让下一个人一句 `model_dump()` 就能顺带改 agent
归属人，而这个函数的名字不会提示审查者去查鉴权。

`identity_record_updated` 也不再在本路由推导——那是 `AgentProfileWrite` 上的
property，两个路由各推一份就是同一条规则的两处漂移点（本次已经因此出过一次不对称）。

## 2026-08-18 (四改) — import 改指领域包

`apply_agent_profile_change` 不再从 `module.awareness_module` 拿，改从
`narranexus.platform.agent_profile`（见 [[_overview]]）。本路由不再 import 任何
Module——它 import 的是一个核心领域包。行为不变。

## 2026-08-18 (二改) — 错误文案改读 `unapplied_fields`

`not_applied` 分支原来读 `result.updated_fields`，而那个字段在成功分支的含义是
「写了哪些」。共享结果已拆出 `unapplied_fields`（见 [[_awareness_writes]] 二改），
这里跟着改。这条分支**当时零测试覆盖**——读错字段只会让文案退化成没有字段名的
`"The update did not persist: "`，不会红。已补
`test_a_transaction_level_failure_names_the_fields_that_did_not_land`。

## 2026-08-18 — `PUT /agents/{id}` 改走共享改名事务，不再自己拼

深圳线下第二轮 P1 的**另一半**。#320（下一条）修的是"改成了却报失败"，本条修的是
"改成了但 agent 不认"：本路由写列、刷名录，却**从不追加身份更正**，而
[[_awareness_writes]] 的改名事务从 2026-08-04 起就把那条更正当作改名的必要组成。
结果 profile 里停着上一次改名留下的、**方向相反**的平台记录（详见那份 md 的取证）。

改法：整段内联写入换成一次
`apply_agent_profile_change(db, agent_id, new_name=…, new_description=…,
extra_updates={"is_public": …})`。原来那段做的每件事（归一、等值短路、回读而非
rowcount、无条件刷名录）都在事务里，**行为不变**；多出来的是身份更正。

- 失败按 `result.error_kind` 结构化映射成本路由的话术（`not_found` / `not_applied`
  各自保留原文案），**不匹配英文散文**——那串是给模型读的，措辞会变。
- `is_public` 作为 `extra_updates` 同行写入，保持单次行写。
- **回读核对留在本路由**，且核对的是 `requested` **全部**字段，不是事务写过的那些。
  两者问的不是同一件事：事务问「我写的落了没」，路由问「调用方要的状态成立没」。
  一个在读时就相等的字段不会进事务的清单，但并发写入方（另一个标签页、或 agent
  自己的 `update_agent_profile`）仍可能在读—写—回读的窗口里把它挪走。委托的第一版
  把这条悄悄收窄成"只查写过的"，全量测试没红——因为它**当时没有测试钉住**。现已
  补 `test_a_field_that_needed_no_write_is_still_verified_against_the_row`。
- `sync_agent_discovery` 与 `agent_field_matches` 的 import：前者移除（本文件不再
  直接持有），后者**保留**（上一条那个核对要用）。**测试打桩点也跟着搬**——
  `test_a_no_op_re_save_still_refreshes_the_peer_directory` 原来 patch
  `auth_mod.sync_agent_discovery`，路由不再 import 该名字后，那个 patch 会
  **静默地什么都没监视到**并对任何行为放行。已改为 patch 定义模块
  `message_bus.agent_discovery_sync`。凡是"路由不再直接 import 的符号"，
  打桩点都必须跟着走，否则测试从守卫退化成装饰。

## 2026-08-17 — `update_agent` 判「改没改成」靠回读，不靠 rowcount

`PUT /agents/{agent_id}` 原来是 `affected_rows > 0` 才算成功，否则回
`success=False, error="No changes made"`。而 `AgentRepository.update_agent`
返回的是 `cursor.rowcount`——**SQLite 数 MATCHED 行，MySQL 数 CHANGED 行**。
于是「把值改成它已经是的那个值」在 SQLite 上返 1（成功），在 MySQL 上返 0
（被判失败）：**只在 cloud 上错**。用户看到「保存失败」，重试，每次都失败，
而库里一直就是他要的新名字。深圳线下第二轮 P1 就是这个形态（取证：
`agents.agent_name` 已是「小绿」，前端却说没保存成，测试者反复重试）。

同一个陷阱 [[_awareness_writes]] 在 2026-08-05 已经为 agent 侧的
`update_agent_profile` 拆过（写前做值相等短路）；本次是**用户侧 HTTP 那一半**，
当时漏了。两边现在在这个 trap 上同语义——但这是**本次同时改了 agent 侧**才
成立的：那边当时只拆了 no-op 一支，「真有改动而驱动报 0」仍会答失败，本次一并
换成回读判定（见 [[_awareness_writes]] 2026-08-17 条）。

修法（不是给 rowcount 打补丁，是把它从判据里拿掉）：

1. `requested` = 调用方要的字段，**并且已经是入库形态**（`normalize_agent_text`）。
   `update_data` = 其中与当前行真的不同的那些（谓词见下）。全都相同 →
   **一次写都不发**。
   > 更正（2026-08-18）：原文这里还写着「`sync_agent_discovery` 也不发（没有
   > 变化要广播）」。已改成**每个被接受的请求都发一次**。理由是这个调用存在的
   > 初衷：2026-08-04 那条写着「此前注册是『跑过一轮』的副作用，所以刚创建、
   > 或改完名字/描述后**闲着**的 agent 对同伴不存在」——「下一轮会自己重写」
   > 当初就被判定为不够。而 sync 自己吞失败只返回 False（所以本轮给它加了
   > warning），一旦某次失败让同伴目录停在旧名，**原值重存是用户最自然的重试
   > 方式**，它不该恰好是唯一跳过修复的那条路。代价是 no-op 重存多一次
   > discovery 写——而 no-op 重存本身很罕见。测试：
   > `test_a_no_op_re_save_still_refreshes_the_peer_directory`。
2. 发过写之后**回读**，再用同一个谓词核对 `requested` 是否都落到行上。
   rowcount 只进 debug 日志，注明 dialect-dependent、仅供参考。
3. 回读后仍不符 → `success=False`（错误串点名哪些字段没落）。日志级别是
   **WARNING 不是 ERROR**：读—写—回读之间没有 CAS，另一个标签页或 agent 自己的
   `update_agent_profile` 在窗口内写入，就会在这里表现为「不是我要的值」——
   良性的 last-write-wins 不该长得像持久化故障。

谓词 `agent_field_matches` **不在本文件**，在 [[entity_schema]]：它编码的是
Agent 实体的字段等价规则，而 `agents` 行有两个写入方（本路由 +
[[_awareness_writes]]），各写一份比较迟早分歧——事实上分歧当时已经存在
（那边比较 strip 过的值，这边比较原样值）。**决定要不要写**和**核对写没写成**
共用同一个函数，两者不可能各说各话。

归一化连带的一条：入库前 strip，所以「比较说相等」和「行里是什么」永远同形。
另外空名（`""` 或纯空格）现在**被拒**，与 agent 侧 `update_agent_profile` 的
拒绝语义对齐——没有名字的 agent 在所有界面上退回显示裸 `agent_id`，而同一个
输入原来在一条路径被拒、在另一条被存。不放在 schema 的 `min_length`：`"  "`
过得去，得在归一之后判。

`sync_agent_discovery` 的返回值现在接住了：它内部吞掉自己的失败并返回 False，
不接的话「同伴目录还是旧名」与「接口答成功」这两件事事后对不上。

测试：`tests/backend/test_agent_rename_outcome_not_rowcount.py`(路由级) +
`tests/schema/test_agent_field_matches.py`(谓词本身)。前者**强制** MySQL 那种
rowcount 读法（monkeypatch 成返回 0），因为 SQLite fixture 对 no-op 写返回 1，
照原样测会在「本来就不会出这个 bug 的方言」上空过。覆盖：no-op 重存=成功且
**不发写**、真改动但驱动报 0=成功（回读为准）、真的没落库=仍然失败（防止修成
「永远成功」）、可见性开关在同样条件下**必须真的发写**（断言写调用本身而不是
`success`——见下）、空名被拒、首尾空格入库前被 strip、改名后 list 端点能看到新名。

⚠ 回读校验对**谓词自身**的错误是结构性失明的：谓词若错判「已经相等」，则既不
发写、又由同一套逻辑判定「已落库」→ 返回成功、日志无异常。所以谓词必须有自己的
单测，且路由级用例要断言**写调用**（`calls == [...]`）而不是返回的 `success`。

**创建路径同日补齐(review 第二轮)**:`create_agent` 的默认值改成在归一**之后**
判 —— `agent_name = normalize_agent_text(request.agent_name) or "New Agent"`。
`"   "` 是 truthy,先 `or` 会漏过默认串把纯空格存成名字,侧栏行标题直接空白
(比空名退回显示 `agent_id` 还难认)。描述同理走 `normalize_agent_text`。

长度上限**不在本文件判**:它在 [[api_schema]] 的 `_StrippedText` 上,归一之后量,
所以本路由与 agent 侧对同一输入的验收集合一致,且 422 契约不变。中途试过把 cap
搬进路由,打破了「四个写边模型统一 422」那条既有契约,被
`tests/backend/test_agent_request_length.py` 当场抓住 —— 别再往这个方向走。

## 2026-08-13 — netmind_login 在建 token 前先过账户状态闸门

`netmind_login` 在 `create_token` 之前，多一道账户状态判定：状态从 `user`
实体（`upsert_netmind_user` 返回值，本身就是经 `UserRepository.get_user` 的
`WHERE BINARY user_id` 读到的行，故大小写敏感、与停用**写**侧同 collation）取
`user.status.value`，若落在共享的 `NON_TRANSACTING_USER_STATUSES`（从
`narranexus.platform.schema` import，[[entity_schema.py]] 的单一真相源，取代原来
内联的 `{banned, blocked, deleted}` 字面量），记一行 WARNING 后
`raise AuthError(ACCOUNT_SUSPENDED, "Account is not available", status_code=403)`
（见 [[auth_errors]]），**不签 token**。

**大小写敏感 + fail-open**：不再用 `db_client.get_one`（MySQL 默认大小写不敏感
collation，会让 look-alike user_id 绕过闸门）。`role` 不在 `User` 实体上，故单独
用一条 `WHERE BINARY user_id` 的 raw SELECT 读，同样大小写敏感。两处读都裹
try/except **fail-open**（分别退回 `"active"` / `"user"`）：登录绝不能因为状态读
抖动就挂——闸门是拦某个被停用账户，不是变成登录可用性依赖。

同等重要的是：early return **短路掉后续所有 fire-and-forget 登录副作用**
（session 重整、provider/quota 供给等）——这些正是一个被停用账户应当停止消耗的
后台工作。状态值是一个不透明集合，本路由不持有「账户如何走到停用」的任何策略。
这与 [[auth]] middleware 的账户状态闸门是两道互补的关卡：middleware 拦住已有
token 的后续请求，这里拦住停用账户**重新拿 token**。

## 2026-08-12 — reply_language 路由

GET/PUT `/settings/reply-language`(同 analytics 模式);PUT 由 i18n languageChanged 写透 + boot 回填,读方是 [[context_runtime.py]] system prompt 注入。

## 2026-08-12 — funnel 白名单加两个 send-code stage（配前端 #289）

`/api/auth/funnel-report` 的 `_FUNNEL_STAGES` 是**枚举白名单**（caller-controlled 输入的第二道闸，不是自由 tag：未知 stage 直接 400，且 400 在写 `[login-funnel]` 日志之前 return，所以不落痕）。前端 #289 新增三条上报路径（`SignUpDialog.sendCode` / `useNetmindAuth.sendResetCode` / `resetPassword`），对应加入 `signup_send_code_failed` / `netmind_reset_code_failed` / `netmind_reset_password_failed`——**必须与前端同 PR 合**，否则前端上报被 400 静默吞掉（`api.reportAuthFunnel` fire-and-forget），诊断空转。前端 `api.ts` 的 `AuthFunnelStage` 联合类型把这条跨端契约提前到 `tsc`（前端加白名单外的 stage 就编译不过）；`test_auth_funnel_observability.py` 的 `test_all_known_funnel_stages_are_accepted` 兜反向（后端误删/改名已用 stage）——两个守卫互补。

## 2026-08-11 — telemetry consent 端点(与 analytics 并排但语义相反的持久化)

`GET/PUT /api/auth/settings/telemetry`。与 analytics(per-USER,
user_settings 行)刻意不同:遥测同意是 **per-MACHINE 标记文件**
(`~/.narranexus/telemetry_optout`,utils/logging 每次外发都读)——
logging 先于 DB 启动,DB 装不下这个状态。per-machine 带来两条端点
必须执行的边界:多租户云一个用户不得静音整机 → PUT 403、GET
`controllable=false`(那个面由部署 env 治理);`NEXUS_DIAG_SHIP`
显式覆盖时标记写入静默无效 → PUT 409、GET `source=env`。GET 另带
`managed_by: env|cloud|null`——不可控状态要说清**是谁在管**:自托管
多租户装机(source=default 但 cloud mode)若复用 env 措辞,等于把
内置默认归因给一个没人设过的环境变量(预审抓的"归因谎言")。身份
仍走 `_require_request_user`(与邻居 analytics 一致)。
Tests: `tests/backend/test_telemetry_consent_routes.py`。

## 2026-08-10 — cloud signup capture repaired

The new-NetMind-user branch now awaits and keyword-calls analytics. Positional
calls to keyword-only async functions previously raised and were swallowed, so
cloud signup facts never existed. Setup actions are tagged frontend-originated.

## 2026-08-10 — identity telemetry removed

Signup paths persist only the `signed_up` first-party fact. The former
`identify_user` calls and vendor-oriented privacy comments are gone; no user
identity or trait leaves the configured NarraNexus database.

## 2026-08-10 — setup events use the unified analytics route

The legacy `POST /api/auth/funnel` endpoint and its duplicate allowlist were
removed. Setup browser facts now use `POST /api/analytics/events`, gaining the
same session ID, idempotency namespace, rate limit, and payload contract as all
other frontend product events. Auth routes retain only the privacy setting and
backend-owned signup fact.

## 2026-08-10 — create_agent provisioning 提炼到 provision_new_agent seam

原来 create_agent 路由内联的「建 agent 行 + 默认实例 + 发现注册 + bootstrap +
默认技能安装」序列,提炼进 [[provision]] `provision_new_agent`,本路由改为调它。
本路由仍是该序列的**语义来源**,只把非共享部分留在外面:user-existence 校验、
team assignment(#43)、CreateAgentResponse shape。同一 seam 被 MCP 工具与
social-network 路由共用,消除三份漂移复制(PR-2 pre-open review #3)。

## 2026-08-06 — 新增 `GET /api/auth/session`（会话探针）

两个消费方，都在前端：
1. **强制登出前的第二意见**——单个 401 不构成"会话已死"的证据，
   [[sessionGuard.ts]] 先探这里，探针也说死了才拆会话（2026-08-02 线下
   活动上，一个 401 就拆了整个 SPA）。
2. **到期预警**——返回 `expires_at`，让 UI 在 JWT 死之前就能提示
   （[[tokenExpiry.ts]]）。

刻意**不查库**：它跑在每一次可疑 401 上，一批 401 不能变成一批查询。
能走到这个 handler 本身就是答案（JWT 校验发生在 middleware）。
**不进** `AUTH_EXEMPT_PATHS`——豁免了就回答不了它存在的那个问题。

同时把本文件两处 `HTTPException(401)` 换成 `AuthError`：netmind-login 的
"Invalid NetMind token" → `netmind_token_invalid`（登录失败，本来就还没有
会话可言），analytics 的 `_require_request_user` → `identity_unresolved`。

## 2026-08-04 — 创建不再写占位符描述；创建/更新即时进同伴名录

两处（P1 段02）：

1. `agent_description` 的默认值从 `"A new agent ready for configuration"` 改成
   **空串**。那句填充被 bus 名录快照、被当事实报给同伴，也被当作 agent 自己的
   自述注入提示（见 [[entity_schema]] / [[basic_info_module]]）。
2. 创建成功（默认 instance 建好之后）和更新成功之后，都调
   [[agent_discovery_sync]] 的 `sync_agent_discovery`。此前注册是"跑过一轮"的
   副作用，所以刚创建、或改完名字/描述后闲着的 agent 对同伴不存在——票上目标 2。
   两处都是 best-effort：agent 该建的建了、该改的改了，发现元数据刷新失败不影响
   请求成功。

创建那次刻意放在 `InstanceFactory.create_agent_level_instances` **之后**，
capabilities 快照才不是空的。

## 2026-07-31 — active_run 富集只认 chat/manyfold 来源

`/api/auth/agents` 的 running 行 SELECT 加 `` `trigger` IN
('chat','manyfold') ``：trigger run（lark/team/job）现在也是
state='running'（run 可观察性），不滤的话 ChatPanel 的 auto-reconnect
会把网页聊天接到一条 Lark turn 上。chat/manyfold 恰好是 BackgroundRun
的两个面 —— 本富集从来描述的就是它们；其余 run 走 WS 观察端点
（tail-follow）看，不进聊天页。依赖 step 0 的诚实 trigger 标签
（[[step_0_initialize]]）。

## 2026-07-29 — 删除 agent_cli_sessions 的级联清理(T7)

删 agent 时的 5b 步(`DELETE FROM agent_cli_sessions WHERE agent_id = %s`)移除。

它当初补上的理由值得记住(2026-07-28):那张表加进来时**漏了级联**,于是句柄比
agent 和它的工作区都活得久,一个被回收的 `agent_id` 会继承一个指向已死 transcript
的句柄,而且行只增不减没人清理。

现在表本身已摘掉注册([[schema_registry]]),这段 `DELETE` **留着反而会报错**。
根因也不复存在:没有任何按 agent_id 存的持久物——transcript 每轮写、每轮删。

## 2026-07-28 — delete_agent 级联补 agent_cli_sessions

`agent_cli_sessions`（resume 句柄表，见 [[schema_registry.py]]）加表时漏了级联，
`delete_agent` 的 leaf-first 扫除里没有它。按既有形状补在 `instance_jobs` 之后
（同为 agent_id 直接键的运行期表）：显式 DELETE + 计数进 `deleted_counts`，
零行时不入 stats（沿用本函数惯例）。

漏扫的后果有两层：句柄行**比 agent 和它的 workspace 活得更久**，agent_id 若被回收
就会继承一个指向已删 transcript 的句柄；而且没有任何别的机制会清它们，只会无限
累积。测试：tests/backend/test_delete_agent_queue_cascade.py（含"别的 agent 的句柄
必须存活"与"零行不进 stats"两条）。

## 2026-07-28 — 两个 provisioner 串行，免费额度必须先落地

新用户登录后 agent 必挂 "Claude API error: unknown"。真因是并发：登录路径
上原本并排 fire-and-forget 了两个 provisioner，它们各自读"这用户是否已有可
用配置"来决定要不要绑 slot，而两边都在对方写入之前读到了空——典型 TOCTOU。
NetMind 那条要先铸 key 所以后完成，于是把 slot 改绑到了用户自己那张**刚开
户、零余额**的 Power 卡上；$10 钱包躺着没人用，每次调用都报上游错误。

修法是串行：`_provision_providers` 先 await 免费额度，再 await NetMind。
免费额度排第一不是随手定的顺序——它是我们**确定有余额**的凭据；新开的
Power 账户没有额度，而已充值的老账户不受影响（NetMind provisioner 见到完整
配置时本来就只注册不抢绑，串行之后它才真的能见到）。

拆成 `_provision_providers`(协程) + `_schedule_provider_provisioning`(调度)
两层，是为了让测试能直接 await 协程观察顺序：调度是 fire-and-forget，而
TestClient 一返回响应就关掉 event loop，让出过一次的 task 根本跑不完。
**测试里两个 fake 都必须 await**——真 provisioner 的 HTTP/DB 挂起点正是竞态
的成因，不 await 的 fake 在 `asyncio.gather` 下也会串行执行，对着坏版本一样
绿（已用 gather 回插验证过）。

免费额度的开通也不再只在 `is_new` 里跑，改成每次登录都跑：provisioner 靠
key alias `free::{user_id}` 自带幂等，无条件重跑能让首次撞上钱包服务故障的
用户在下次登录自愈，而不是永久卡死。

## 2026-07-28 — 首登不再播种 token 配额，改为开钱包

首次 NetMind 登录时的 `quota_service.init_for_user` 换成
`schedule_ensure_free_tier_provider`：开一个 $10 钱包并把它注册成一张普通
provider 卡。

因此登录响应里的 `has_system_quota` / `initial_*_tokens` 三个字段删除 ——
开通是 fire-and-forget 的（登录绝不能被钱包服务拖住或拖垮），响应时刻它
还没跑完，报任何值都是猜的。调度调用本身也包了 try/except：这条路径上任何
异常都不许冒泡成登录失败。

## 2026-07-23 — delete_agent cascades memory_consolidation_queue

New step 7c: `DELETE FROM memory_consolidation_queue WHERE agent_id = ?`.
Without it the consolidation worker idle-triggered the dead agent's scopes
on every poll and spammed "no owner row" warnings forever (see
[[memory_consolidation_worker.py]] 2026-07-23 for the worker-side
self-heal). Test: `tests/backend/test_delete_agent_queue_cascade.py`.

## 2026-07-21 — create_agent 默认技能装机(stage 9)

创建 agent 成功后 fire-and-forget 一个 asyncio task 调
`SkillMarketplaceService.install_defaults`(带 done-callback,教训 #2)。
非阻塞、非致命:registry 离线/未部署时优雅跳过;builtin 物化机制不受影响,
仍是离线兜底。


## 2026-07-17 — `/api/auth/agents` first-paint sort by recent conversation

`get_agents` now sorts the returned `AgentInfo` list by activity
before responding: two stable passes (agent_id asc, then activity
desc) put the most-recently-active conversation on top. Activity =
`max(last_assistant_at, created_at)`; both are `format_for_api`
fixed-width ISO-UTC strings ("...Z"), so lexical string compare is
correct — no datetime parsing needed. This is the pre-hydration
BASELINE only: the frontend re-sorts with the same rule PLUS fresh
local session activity (see [[agentGroupUtils]] `sortAgentsByActivity`),
so an agent still jumps the instant the user talks to it. The SQL
`ORDER BY agent_create_time DESC` is retained purely as deterministic
input order for the enrichment queries; the Python sort decides the
final order.

## 2026-07-13 — netmind-login 门禁改挂 power 轴（本地双模式登录）

`netmind_login` 的可达性从 `_is_cloud_mode()` 改成 `is_power_login_enabled()`
（[[deployment_mode]]），于是本地部署开启 `NARRANEXUS_ENABLE_POWER_LOGIN` 后
也能用 NetMind(Power)账号登录,与纯本地用户名登录并存,用户自选。到达 handler
后 line ~235 的 `schedule_ensure_netmind_provider`（见 [[netmind_provisioner]]）
照旧触发,本地也会自动铸 Power provider + slot。**注意:`login()`（用户名登录）
与 `create_user()` 仍挂 `_is_cloud_mode()`**——它们表达"云端禁用用户名/建号"这一
安全语义,本地双模式必须保留,不能改成 power 轴。

## 2026-07-10 — NetMind login auto-registers the user's provider

`netmind_login`, right after issuing the app JWT + `_schedule_login_rearm`, now
fire-and-forgets `schedule_ensure_netmind_provider(user_id, netmind_token)` (see
[[netmind_provisioner]]). Cloud login IS NetMind login, so the user's NetMind
provider is minted+registered automatically — no manual "use this account"
button. Non-fatal by construction: login never blocks on or fails from NetMind
minting; the provisioner self-guards on the feature flag and only activates slots
when the user has no active config (register-always, activate-if-fresh).

## 2026-07-09 — agent-delete cascades agent_slots

The delete-agent cascade (step 14f, before deleting the agent row) now
``DELETE FROM agent_slots WHERE agent_id = %s`` so a removed agent leaves no
orphan per-agent LLM overrides.

## 2026-07-07 — `trigger` is a MySQL reserved word: must be backticked

The `last_assistant_preview` window query filters on the `trigger` column.
`trigger` is a **MySQL reserved word**; written bare it raises `(1064, ...
near 'trigger IS NULL ...')` on prod (MySQL) — 2585 WARNINGs in 2 days,
sidebar previews silently empty. SQLite tolerates a bare `trigger`, so local
dev never caught it. Fix: `` `trigger` `` (backticks work on both dialects).
Any raw SQL touching this column must quote it — see the same fix in
[[_dashboard_helpers]]. Regression: `tests/backend/test_trigger_reserved_word_sql.py`
emulates MySQL's rejection in-process (SQLite can't reproduce it).

## 2026-06-23 — sidebar preview excludes group-chat replies (forward only)

`/api/auth/agents`' `last_assistant_preview` window query filters
`trigger != 'message_bus'`. New team group-chat runs are tagged at creation
([[step_0_initialize]] / [[models]]), so their replies are excluded → previews
stay clean **going forward**.

**Why historical rows can't be filtered** (investigated 2026-06-24): the root
leak is that a message-bus run records its reply under the agent's *regular*
narratives (default `*_default_N-*` AND topic `nar_*`), identical to a 1:1
reply — same `trigger_source` (the user), no marker. Most replies never reached
`bus_messages` (e.g. rabbit: 2 rows vs many leaked events), so content-matching
is incomplete; and the same reply is duplicated across default + topic
narratives, so neither narrative-id nor actors separate them. Pre-tag previews
therefore can't be cleaned by query — they age out as the agent has genuine 1:1
activity, or the user clears history.

Real fix (pending): stop bus runs writing into 1:1 narratives — route them to a
dedicated team-room narrative in narrative selection.

## 2026-06-11 — identity hardening: create_agent / timezone / onboarding

The last three routes that trusted a client-supplied user id now derive identity from auth_middleware via `resolve_current_user_id`: POST /agents (body created_by removed — clients could create agents under anyone's account), POST /timezone and GET+POST /onboarding (body/query user_id removed). Old clients sending the extra field are harmless (pydantic ignores unknown fields); old clients omitting X-User-Id/JWT get 401. scripts/spikes/bench_narrative_models.py updated to send X-User-Id.

## 2026-06-11 — legacy cloud auth removed (invite codes retired)

/login is local-only now (cloud -> 404, points at netmind-login); /register deleted outright; /create-user gained a cloud 404 guard (it was an unauthenticated open account-creation endpoint sitting in AUTH_EXEMPT_PATHS — known hole, now closed). Invite-code mechanism retired entirely per 2026-06-10 owner decision (signup == first NetMind login, everyone gets the free-tier quota): routes/invite.py and routes/admin_invite.py deleted, InviteCodeRepository and invite_code_gen deleted, INVITE_AUTO_ISSUE_CAP / INTERNAL_INVITE_SECRET config gone. The invite_codes TABLE survives — it holds the old-user-id -> email mapping the legacy-user migration script needs.

## 2026-06-11 — POST /api/auth/netmind-login (Phase 1 user-system unification)

New cloud-only login endpoint: verifies a NetMind loginToken via `NetmindAuthClient` (one network call to NetMind's /user/balance), lazily upserts the local user (`UserRepository.upsert_netmind_user`, user_id = NetMind userSystemCode), seeds the free-tier quota on FIRST login (registration no longer exists — first login is registration; invite codes are gone per 2026-06-10 decision), then issues NarraNexus's own JWT. Error mapping: bad token -> 401, NetMind unreachable/contract drift -> 502 (never disguised as a credential failure). `_get_netmind_auth_client()` is module-level for test monkeypatching. The legacy /login (cloud password branch) and /register are slated for removal in the same feature branch.


## 2026-06-10 — run-liveness helper moved to background_run.py (shared)

The `_parse_db_utc` / `_run_is_live` heartbeat-freshness rule (running
events row trusted only while `last_event_at` is within 3 missed beats)
moved to `background_run.py` as `parse_db_utc` / `run_is_live`, because
the WS reconnect path now needs the SAME answer to "is this run actually
alive?" (see websocket.py 2026-06-10 entry — zombie running rows must be
reported as `run_ended`, not reconnect-looped). auth.py keeps a local
`_run_is_live = run_is_live` alias; behavior of the agents-list
active_run filter is unchanged.

## 2026-06-08 — account deletion clears memory_* by agent_id

Account deletion dropped `instance_social_entities` from `instance_sub_tables` and added a loop deleting every `memory_<kind>` table by agent_id (using `MEMORY_KINDS`), so a deleted account leaves no orphan rows in the unified memory store.

## 2026-06-08 — analytics opt-out endpoints

Added `GET /api/auth/settings/analytics` and `PUT /api/auth/settings/analytics`
for the frontend privacy toggle. Both delegate to `UserSettingsRepository`
(new dependency added this task). The GET returns `{"opted_out": bool}` where
the absence of a user_settings row means `false` (opted in by default). The
PUT accepts `{"user_id", "opted_out"}` and upserts the row.

`SetAnalyticsOptOutRequest` is a small Pydantic `BaseModel` defined inline
(not in `schema/` — it has two fields and no reuse elsewhere). `BaseModel` and
`UserSettingsRepository` are imported at the top of the file alongside the
existing imports.

Tests: `tests/backend/test_user_settings_routes.py`.

## 2026-06-08 — funnel: signed_up event

`create_user` records `track(EVENT_SIGNED_UP)` on the success path. The fact is
best-effort, stays in the configured NarraNexus database, and never interrupts
account creation.

`create_agent` carries no analytics instrumentation. `EVENT_AGENT_CREATED`
was removed in the 2026-06-09 funnel redesign; create_agent is not a
tracked funnel milestone.

## 2026-05-21 — onboarding checklist endpoints

Added `GET /api/auth/onboarding` + `POST /api/auth/onboarding` for the
new-user onboarding checklist card (cloud version). State lives inside
`users.metadata` under the `onboarding_progress` key — no new table.

Design points:
- **Write-once-true**: `POST` only applies fields explicitly `True`; None
  and False are ignored, so a completed step can never be reverted. This
  is deliberate — the checklist must not oscillate when a user creates
  their first agent then deletes it.
- **Merge, don't clobber**: `users.metadata` is a shared JSON blob, so the
  handler reads the full dict, updates only the `onboarding_progress`
  sub-key, and writes the whole dict back (`_read_onboarding` helper +
  `_ONBOARDING_METADATA_KEY` constant).
- `provider_configured` is **not** stored — the frontend derives it live
  from provider count (that step is gated by SetupPage before the card
  shows). Only `first_agent_created` / `template_applied` / `dismissed`
  are persisted.

Sits next to `/api/auth/timezone` — both are JWT-gated user-scoped
settings endpoints. Tests: `tests/backend/test_onboarding.py`.

## 2026-05-19 — `/api/auth/agents` 附加最近一条 assistant 回复（NM sidebar preview）

每个 `AgentInfo` 现在带 `last_assistant_preview` + `last_assistant_at` 两个字段，供前端左边栏第二行显示"这个 agent 最近说了什么"。

实现走窗口函数：`ROW_NUMBER() OVER (PARTITION BY agent_id ORDER BY created_at DESC)`，单条 SQL 一次性拿到列表里每个 agent 的最近一条非空 `events.final_output`。已有的 `idx_events_agent_created` 索引直接 cover 这个查询，不需要新加索引。过滤 `final_output IS NOT NULL AND final_output != ''` 把崩在中途的 run 和空回复都排掉。

server 端把 `final_output` 拍平空白后截到 200 chars（前端再切到 60，多出来的 200 给前端将来调宽度留余量）。失败仅 warn-log，不阻塞 list 返回——和 active_run 一样定位为增强字段。

## 2026-05-14 — register() 改用 DB 邀请码（替换全局 INVITE_CODE）

`register()` 不再比对 `backend.auth.INVITE_CODE` 全局环境变量（该常量已
删除）。新流程走 `InviteCodeRepository`：

1. `get_by_code` 快速预检——码存在且 `status=='issued'`，否则返回明确错误
   （已用 / 失效 / 无效）。纯为 UX，不是真正的 gate。
2. 校验密码、用户名、user 不存在（顺序不变）。
3. `consume(code, user_id)` —— 单条带条件 UPDATE（`WHERE status='issued'`），
   原子消费 issued→used。并发抢同一码只有一方 affected==1。
4. insert user；失败则 `revert_consume` 把码退回 issued，不白烧。

注册不再"全局开关"——有没有可用的码由 `invite_codes` 表决定。Mode B 的
发码 / cap / waitlist 全在 `backend/routes/invite.py` + `admin_invite.py`。
设计记录为作者本地 worklog，不入库；关键决策已写在上文。

## 2026-05-13 — `/api/auth/agents` 返回 active_run 字段（Phase C）

GET 端点为每个 agent 附带 `active_run: ActiveRunInfo | null`——前端
据此显示 Agent 卡片上的"Running"徽章（复用 Jobs status badge 的视觉
pattern）。

实现：在 agents 主 SELECT 之后再做一次 SELECT 把所有 `agent_id IN
(...)` 且 `state='running'` 的 events 行一次性查出来（IN-列表合并避
免 N+1），按 agent_id 索引到 dict，再 zip 进 AgentInfo。失败仅
warn-log，不阻塞 list 返回——active_run 是增强而非核心。

新加的 `ActiveRunInfo` Pydantic 模型在 `schema/api_schema.py`，导出在
`schema/__init__.py`。Spec: `2026-05-13-agent-runtime-lifecycle-and-stream-resilience-design.md` §4.1.8

## 2026-04-16 addition — quota seeding on register

Successful `/api/auth/register` in cloud mode now calls
`app.state.quota_service.init_for_user(user_id)` after the user row is
inserted. The call is defensive:
- QuotaService disabled (local / feature off) → returns None, response
  still succeeds with `has_system_quota: false`
- DB failure during quota insert → logged, registration still succeeds
  so the user doesn't lose their account over a quota-subsystem bug

The response shape gained `has_system_quota`, `initial_input_tokens`,
and `initial_output_tokens` fields. The frontend RegisterPage uses them
to render a one-shot welcome toast on successful cloud-mode registration
— skipped silently in local mode where the flag is false.

# routes/auth.py — 用户认证与 Agent CRUD 路由

## 为什么存在

这个文件承担了两个职责：用户认证（登录、注册）和 Agent 的完整生命周期管理（创建、更新、删除、列表）。Agent CRUD 放在 auth 路由下而不是 agents 路由下，是因为这些操作需要用户身份验证（"这个 agent 属于谁"），在概念上更接近用户管理而非 agent 资源操作。

## 上下游关系

- **被谁用**：`backend/main.py` — `include_router(auth_router, prefix="/api/auth")`；前端登录页、Agent 管理页
- **依赖谁**：
  - `AgentRepository` — Agent 的基础 CRUD
  - `UserRepository` — 用户的增删查、last_login 更新、timezone 更新
  - `InviteCodeRepository` — 注册时校验 + 原子消费邀请码
  - `backend.auth` — `hash_password`、`verify_password`、`create_token`、`_is_cloud_mode`
  - `narranexus.platform.bootstrap.template.BOOTSTRAP_MD_TEMPLATE` — 创建 Agent 时写入工作区的初始化文件
  - `narranexus.platform.settings.settings.base_working_path` — Agent 工作区根目录

## 设计决策

**登录接口的双模式**

登录接口在 local 模式下只需要 `user_id`（不校验密码），在 cloud 模式下需要 `user_id + password`，返回 JWT token。同一个接口，根据 `_is_cloud_mode()` 的返回值走完全不同的逻辑路径。这让前端可以调用同一个接口，通过响应里是否有 `token` 字段来判断当前模式。

**注册只在 cloud 模式可用**

`register` 接口在 local 模式下直接返回错误。Local 模式下用户只能通过 `create-user`（管理员操作）创建账号。Cloud 模式下用户通过 invite code 自助注册。

**Agent 删除的级联顺序**

`delete_agent` 按"从叶到根"的顺序删除：先删动态 Memory 表（按实例/Narrative ID）→ 删 Jobs → 删 Instance-Narrative Links → 删各种实例子表 → 删 Module Instances → 删 Events → 删 Narratives → 删 MCP URLs → 删 agent_messages → 删工作区目录 → 最后删 Agent 本身。这个顺序是为了避免外键约束失败，同时确保没有孤立数据残留。

动态 Memory 表（`json_format_event_memory_*` 和 `instance_json_format_memory_*`）需要运行时发现，因为它们的表名包含模块类型后缀，不是固定的。代码里对 SQLite 和 MySQL 分别用不同的系统表查询语法来发现这些表。

**Bootstrap.md 触发首次配置**

创建 Agent 时会在工作区写入 `Bootstrap.md`，Agent 在首次运行时检测到这个文件并执行初始化流程。`bootstrap_active` 字段在 GET agents 接口里通过检查文件是否存在来计算，是文件系统状态而非数据库字段。

## Gotcha / 边界情况

- **Agent 列表使用原始 SQL**：`get_agents` 直接构造 SQL 查询（`WHERE created_by = %s OR is_public = 1`），而不是通过 `AgentRepository`。这打破了 Repository 模式的封装，但允许更灵活的可见性规则（自己的 + 公开的）。
- **`password_hash` 的遗留用户处理**：登录时如果 `user` 对象上没有 `password_hash` 属性，会再次查原始 DB 行。这是为了兼容通过 `create-user` 创建的无密码用户（local 模式遗留）。
- **工作区目录和 agent 是 1:1 绑定的**：目录名是 `{agent_id}_{user_id}`，删除 agent 时会删掉整个目录（包括所有上传的文件）。这个操作不可逆。

## 新人易踩的坑

`delete_agent` 里的 `stats` 字典只记录被实际删除的行数（`cnt > 0` 才写入），如果某个表里没有这个 agent 的数据，该表不会出现在删除统计里。不要用 `stats` 的 key 来判断"是否执行了删除操作"，正确的理解是"哪些表删除了至少一行"。

## 2026-08-05 — 注册/登录漏斗观测（[signup-funnel]/[login-funnel]）+ /funnel-report

Base #11 recvre9LlfwXAP + #12 recvre9LlfyyxT 的观测修复,三层：

1. **signup 路由**：限流命中（send-code / signup）与 policy reject 现在都落
   `[signup-funnel]` 日志;上游 refusal 的细节日志在 register client 源头
   （见其 mirror）,路由层不重复。
2. **netmind-login**：NetmindAuthError → 401 之前落 `[login-funnel]` warning,
   携带 client 层带出的上游 status+msg（永不带 token）。
3. **POST /auth/funnel-report（新,pre-auth）**：浏览器直连 NetMind 的登录步骤
   失败时,服务端本来一无所知——前端 fire-and-forget 报到这里,落
   `[login-funnel] client stage=... email=... detail=...`。
   - **与既有 POST /auth/funnel 的区别**：那个是登录后的 setup_* 产品埋点
     （走 analytics）;这个是**登录不成的人**的故障上报（走日志）。
   - 防滥用：stage 白名单（3 个）、detail ≤300 且去换行（防日志伪造）、
     per-email 限流 10/min——超限**静默 200**（诊断通道绝不在用户已经在看的
     失败上再叠一个错误）、local mode 404、无 DB 写入、无可探测响应体。
   - 必须同时进 backend/auth.py 的 AUTH_EXEMPT_PATHS（上报者按定义没有
     session）,有测试钉住。

测试：tests/backend/test_auth_funnel_observability.py（13 条）。

### 2026-08-05 R2（review 修正）：限流按「被消耗的资源」选 key + 丢弃可见化

R1 的 per-email 限流 key 选错了资源维度（review 指出）：/funnel-report 消耗的
是**全局日志**,不是 per-email 的什么东西——email 是调用方自己填的,换 email
即换桶,对攻击者形同虚设;OAuth 失败 email 恒空,全世界共享一个 anon 桶,
真故障时通道自己变成无声采样器。修正：
- 三层桶全过才记：global 120/min（XFF 轮换也打不穿的兜底）+ per-IP 30/min
  （XFF 首跳,Caddy 前置;不在代理后时可伪造——所以才有 global 层）+
  per-email(或 IP) 10/min。
- 丢弃对用户仍静默 200,对运维不再静默：每分钟一条
  `[login-funnel] dropped N client report(s)` 汇总。
- `signup_ui_error` stage 删除（无调用方,铁律 #2）。
- 未做（有意）：/funnel-report 与 1600+ 行处的 /funnel 物理相邻——纯搬家
  diff 噪音大于收益,mirror 里两者区别已写死。

### 2026-08-05 R3（review 修正）：桶顺序即不变量 + XFF 从右数

R2 两个残留（review 指出）：
- **求值顺序反了**：global 桶排第一意味着每个请求先扣 global 再判窄桶——
  单客户端（一个前端重试 bug 就够）能把 global 120/min 打空,全员真实上报
  整分钟被丢。改为窄桶在前、global 殿后：被窄桶拒掉的请求碰不到 global。
  测试钉住：per-IP 拒绝的请求不得消耗 global 配额。
- **XFF 首跳是调用方填的**：本部署链路 client→caddy→nginx 两跳都是**追加**
  （nginx $proxy_add_x_forwarded_for、caddy 无 trusted_proxies）,首跳伪造
  即换桶。改为从右数第 `_TRUSTED_PROXY_HOPS`(=2) 个（edge 亲手追加的那个）;
  链条短于预期（本地/单条伪造）回落 socket peer,绝不信调用方文本。
- 丢弃汇总窗口语义修正：last_log 进程启动即打戳（首个丢弃不再伪装成整窗
  汇总）,日志带实际跨度;`monotonic` 提到模块顶。

### 2026-08-05 R4（review 修正）：per-IP 桶置顶——key 可信且有界的排最前

R3 把 email 桶排第一,但 allow() **拒绝时也会分配 key 的 deque**——未认证
洪水每请求换一个合法格式邮箱,就每请求在 _deques 落一个新 key（O(n)
cleanup 在共享 event loop 上）。旧顺序里这是被 global 短路挡住的,重排时
丢了这层。终版顺序=职责列表=求值顺序：**per-IP（key 由 edge 追加,不可
伪造、单攻击者一个 key,给身后一切封顶）→ per-email → global 殿后**。
测试钉住两个不变量：per-IP 拒绝不消耗 global、不在 email 桶分配新 key。
_TRUSTED_PROXY_HOPS 加 env 覆盖（FUNNEL_TRUSTED_PROXY_HOPS,默认 2）——
它是钉进应用代码的部署拓扑常量,caddy/local/ 的 per-env 路由在本仓视野外,
加/减一跳必须同步,否则 per-IP 静默塌成第二个全局桶;注释改为只依赖跳数
（append 或 overwrite 语义下从右数都成立）。丢弃计数残留挂到下个窗口的
行为维持现状（review 认可,span 如实标注）。

### 2026-08-05 R5+R6（review 修正）：HOPS 钳制下界 + 桶顺序两条不变量说实话

- `FUNNEL_TRUSTED_PROXY_HOPS` 经 `_parse_trusted_proxy_hops` 钳制 ≥1：
  hops=0 时 `parts[-0]==parts[0]`（调用方写的首跳）且 `len>=0` 恒真——
  回落保护永不触发,空 XFF 直接 IndexError 500;0 恰是「无代理部署」最自然
  的填法。空串/垃圾值回默认 2,不在 import 期炸 backend（executor_reaper
  先例）。解析函数纯化（raw: Optional[str]）,6 个边界有测试。
- `_rate_limiter.allow()` 拒绝路径不再分配 key——**防御性保证,不是封顶**
  （R5 曾把它写成"病根修复",R6 按 review 纠正）：limit>0 下新 key 永远
  被放行,放行即分配,key 增长在放行路径。**桶顺序承载两条不变量,都是
  load-bearing**：① per-IP 置顶 = caller-chosen key 分配的唯一封顶
  （每 IP 每窗口 ≤30 个 email key,重排即 R3→R4 回归复发）;② global
  殿后 = 预算不变量。test_ip_bucket_rejection_allocates_no_caller_chosen_keys
  就是①的 order guard——重排会红,红的是重排不是测试。
- 运维反向指针：deploy 仓 nginx.conf/Caddyfile 侧加注释指回本常量（deploy
  仓单独 commit,本仓注释已互指并标明该文件不在本仓）。

## 2026-08-19 — 登录路径挂载 onboarding 引导 Agent 供给

`_schedule_guide_agent_provisioning(user_id, *, is_new)`：三个登录入口
（`netmind_login`、本地 `login`、本地 `create_user`）在成功路径尾部
fire-and-forget 调 `backend.onboarding.provisioning.ensure_guide_agent`
（create_task + done_callback，incident lesson #2 模式；铁律 #21：消费方
只有登录路由，子包住 backend 侧）。要点：

- **每次登录都调、不只 `is_new`**（与免费额度 provisioner 同理由）：
  ensure 内部有用户级 write-once 幂等标记——**顶层键**
  `users.metadata.guide_agent_provisioned`，不嵌在 `onboarding_progress`
  里，因为本文件的 `POST /api/auth/onboarding` 会整块替换那个子 dict，
  嵌套标记会被第一次 UI 建 Agent 清掉（→ 永久重复供给 + 用户删掉引导
  Agent 后复活）。回归测试：
  test_onboarding.py::test_post_never_clobbers_the_guide_agent_marker。
  热路径只是一次用户读；"每次都调"是存量零 Agent 用户补领的通道。
- **is_new 贯穿**：netmind_login 传 upsert 的 is_new，本地 login 恒
  False，create_user 恒 True——`backend/onboarding` 的 BACKFILL 刹车
  （`NARRANEXUS_ONBOARDING_GUIDE_BACKFILL`，**默认关**）用它把"存量零
  Agent 回访补领"做成 ops 显式开启的动作，新注册不受该刹车影响。
- **调度前先查 kill-switch**（`NARRANEXUS_ONBOARDING_GUIDE_AGENT`，默认
  开）：关掉时连 task 都不建。测试套件在 tests/conftest.py 全局置 0，
  避免无关登录测试背后真跑 provisioning；guide 测试自行 setenv 开启。
- **前端同门**：netmind_login / create_user 响应带
  `guide_agent_provisioning=_guide_agent_feature_on()`——前端的"你的第一
  个 Agent 已就位"coachmark 以它为门，拉下服务端 kill-switch 时 UI 不会
  承诺一个永远不出现的 Agent。
- **必须排在 suspended 门禁之后**（netmind_login 里在
  `_schedule_provider_provisioning` 之后）：被停号的登录 403 早退，
  永远到不了这个钩子（test_suspended_account_never_reaches_the_hook）。
- 测试：tests/backend/test_guide_agent_login_hook.py（三入口调度含 is_new
  取值、kill-switch 零调度、provisioning 崩溃不影响登录响应）。

## 2026-09-04 · services + host hooks (batch 3c.6)

`_schedule_login_rearm` is async and fires `onDidChangeUserRunnability`; builtin.job's hook schedules the re-arm (login still responds immediately).

## 2026-09-04 · `module_registry` replaces `MODULE_MAP` (batch 5d)

The registry view is the only module table; usages renamed.

Merged origin/dev (#382 creation studio, #383 onboarding/profile/import) on 2026-09-06; dev's changes ported onto the new `narranexus.platform` paths.

Fix 2026-09-07 (fresh-install journey): `_mark_first_agent_created()` flips the onboarding step when an agent is created through the API (best-effort, idempotent), so the welcome flow does not depend on the frontend posting it.

## 2026-09-07 — agents directory channel UNION is fully parameterised

The bound_channels enrichment builds its UNION through channel.binding_tables.bound_channels_query: channel names and agent ids are parameters, derived tables use index aliases. Behaviour (one query, owned agents only, degrade to [] on failure) unchanged.


## 2026-09-07 — `_funnel_client_ip` 提升成共享模块 `backend/routes/_client_ip.py`

跳数常量和取值函数搬去 `backend/routes/_client_ip.py`（导出 `client_ip` /
`TRUSTED_PROXY_HOPS`），本文件 import 它。动机不是整洁：渠道 webhook 的匿名限流
（`backend/routes/channels/generic.py`）新写了一份 `request.client.host`，于是
仓里同时存在两个「客户端 IP 是什么」的答案，而其中一个在云端恒为 nginx 容器地址。
跳数是**部署拓扑属性**，两份跳数就是这个修复要防的漂移，所以它只能有一个家。
上文 R2/R4/R5+R6 记的所有决策（从右数、≥1 钳制、空/垃圾值回默认）原样搬过去，
测试也改成打 `_client_ip`。
