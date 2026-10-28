---
code_file: src/narranexus/platform/narrative/_narrative_impl/updater.py
last_verified: 2026-09-04
stub: false
---

## 2026-08-26 — C3:digest 从 updater 上下文摘除(它在给 continuity 锚点改名)

A1 的修法(把工具动作摘要喂给 updater)有一个没人排查过的第二读者:
updater 写的四个字段(name/description/summary/keywords)同时是
**continuity 每轮读的锚点描述**。digest 让 updater 把线改名成最近的工具
动作("团队群聊自我介绍"→"深圳天气查询"),continuity 读到改名后的锚点,
把用户的正常延续判成换话题——锚点被改写的行同线率 65.3%→56.8%
(McNemar p=0.0002,未改写行 p=0.60,自带对照),每卷净打断 24 次正确
延续,被打断轮 54/55 落进**别的既有线**(作者侧原始数据:重演批
2026-08-26 的 PR2_CROWDING_ANALYSIS)。

不止摘除插入:digest 机器**整段删除**(约 255 行 + 16 条测试),
独立审查按铁律 #2 裁定——无调用方的 public 函数带着完整测试留在库里,
正是曾两次被删的 dead-fork 形态;代码在 git 历史里,恢复只需一条
`git show a9260baa4:...`。

**第二步(digest 的指定后继)的字段契约,在此为准**:新增一个
**补充检索词字段**——BM25 的 searchable_text 读它,**continuity 永远
不读**(C3 的结构化形态);每个写入来源(digest 词/A-kw 救援词/未来
来源)各自封顶,防单一来源淹没词面;上线前**收益判据必须预注册**——
digest 自身的收益本就弱(动作词仅占金标线 BM25 分数 3.4~4.4%,
recall@3 持平),词面缺口须先重新证明。

**残余(未测量,如实声明)**:user input / `final_output[:500]` 走的是
同一条改名路径(`_apply_llm_update` 无条件覆写 name/summary/keywords),
摘除 digest 后还剩多少改写率、其同线率代价多大,**没有数**;测法 =
与 digest 调查同口径的重演 + 按"锚点被改写行"分层 McNemar。结构性
防线 b = 锚点稳定性约束:线已有真实 summary 后,无换话题证据不重写
`name`(`current_summary` 必须继续逐轮走,只有身份锚字段需要稳定)。

回归钉:`tests/narrative/test_updater_context_sections.py` 把
`_build_update_context` 渲染的**每一行的前缀**钉成闭合清单(标题行
原样、`Key: value` 行取键、动态摘要序号行归一为一项)——任何新增的
`context_parts.append(...)`,无论是新小节还是挂在既有小节下的裸行,
都必然改变清单撞红,逼一次 C3 对照;`web.log not in context` 作第二道
内容层守卫。
教训归档:**给字段加新写入源之前,先画全它的读者清单**——description
墓碑(BM25+continuity+主 prompt 三读者)、本案(BM25+continuity 双读者)
已是同一课的第二次学费。

> 下方 2026-08-25 / 2026-08-21 / 2026-08-12(A1)三条与 digest 相关的
> 条目自本日起为**历史记录**:它们描述的 `build_action_digest`、四层
> 脱敏与预算裁剪已随本次删除离开代码库,保留是为了讲清"为什么当初
> 这样建、为什么后来拆"。


## 2026-08-25 — 脱敏第四层(字符串容器)+ 去重计数(PR #361 round 2)

I1:round-1 的 kv 检查被 `isinstance(dict|list)` 挡住,字符串值里的
`LARK_APP_SECRET=...`(key 无害、值无前缀、类型是 str)三层全放行——与 C1
同一血径,不同容器。修复:kv 正则改为无条件跑在渲染文本上,但分隔符后
必须跟 ≥8 字符的**值形状** token——这条约束就是"散文提到 token 不被吃"
的保证("token 用量"后面没有 :=值,不命中),测试双向钉死。
M7:`build_action_digest` 的去重从静默跳过改为计数折叠(行尾 `(×N)`)——
"连续重试 3 次"和"跑了一次"是不同的 turn 状态,且 `_fit_to_budget` 已
承诺不静默丢内容,去重不该是唯一例外。

## 2026-08-21 — 嵌套凭据脱敏（独立审查 Critical #1）

`_render_argument` 原来只对**顶层**参数 key 跑 `_SECRET_KEY_RE`；dict/list 值经
`json.dumps` 直通，嵌套的 `{"args": {"app_secret": ...}}` 两层正则都抓不到
（Lark app_secret 是无前缀字母数字串，`_SECRET_VALUE_RE` 不识别），而 `args`
恰好在 `_BODY_ARG_KEYS` 的 800 字符大额度里。这条路径是本分支才打通的
（event_log 同步之前 digest 恒为空），泄漏形态是"一次 tool_call 参数 → 摘要 →
helper LLM → narratives 行 → 之后每轮系统提示词"，且无回收路径。
修复：新增 `_SECRET_KV_RE`（键值形状 `"key":`/`key=`，非裸子串），**只对
dict/list 值的序列化文本**启用——纯字符串值仍只走 value 正则，防止把
"帮我查 token 用量" 这类正文误伤成 `<redacted>`（那正是 digest 要救的名词）。
两侧各有测试钉住：嵌套必脱敏、正文提及必不脱敏。

## 2026-08-20 — 线名不再带频道标签(K 层)

`_apply_llm_update` 把 `update_output.name` 直接写进 `narrative_info.name`。
helper LLM 拿到的是事件原文,于是它会把 `[From <sender>]` 一起抄进名字。
prod 现存至少四条这样的线:`[From Liam] * 👊 刚甩过去...`、
`[From U082541Q6AX] stop gre...`、`[From o9cq8001z5NQ4n4H2VdvK...`。

**为什么这不只是难看**:名字进了检索面,于是该频道的下一条消息会在**自己这条线**
上以很低的池内 df 命中发送者 token —— 审计 1492 行由此拿到 margin **357.79**,
judge 完全没参与。**前缀先建起磁铁,然后自己飞进去。**

现在写入前过一遍 `utils.text.strip_routing_prefix`;剥完为空则保留原名
(无名线比略带噪声的名字更糟)。`topic_keywords` 这一行**没动** ——
该字段的两支笔叠加设计(A-kw)未定,本次只读不写。

存量四条线归 Owner 的 9080 清理批次,代码侧只保证不再新增。

## 2026-08-14 — `_async_llm_update` 改走 `spawn`

脱离任务从裸 `asyncio.create_task` 换成 `utils.background_tasks.spawn`，名为
`narrative_llm_update:{narrative_id}`。和下面 2026-07-07 那条是同一条路径：当时补的
凭据告警只有在**任务真的跑起来、且失败真的浮出水面**时才有意义，而裸 create_task 这
两点都不保证。
# updater.py — Narrative 更新 + LLM 动态摘要生成

## 为什么存在

`NarrativeUpdater` 负责把一个 Event 写进 Narrative，并随对话演进动态刷新 Narrative
元数据（name / current_summary / topic_keywords / actors / dynamic_summary）。

两条路径：

- **同步基本更新**（`update_with_event`）：追加 event_id、临时写一条 dynamic_summary
  （取 `final_output` 前 200 字），存库。Default Narrative 只追加 event_id 不做别的。
- **异步 LLM 更新**（`_async_llm_update`，仅 main_narrative 触发）：每攒够
  `NARRATIVE_LLM_UPDATE_INTERVAL` 个 event 就 fire 一个 `asyncio.create_task`，调 LLM
  把动态摘要压成结构化 fact sheet。非阻塞主流程。

并发安全是这个文件的核心设计点：`update_with_event` 和 `_apply_llm_update` 都会先
`load_by_id` **重新从库里拉最新 Narrative** 再改，避免拿着流程开头的 stale 对象覆盖掉
并发写入（典型是别的进程刚加的 PARTICIPANT actor）。`_apply_llm_update` 还刻意**只改
LLM 生成的字段（name / summary / keywords），不碰 actors**，保住库里最新的参与者。

辅助 Narrative 目前只做基本更新、跳过 LLM 更新（视角不同，需专门的 prompt，TODO）。
Embedding 那套机器在 2026-06-04 unified-memory 重构时已移除——路由改成 name/summary/
keywords 上的 BM25，相关 DB 列（routing_embedding 等）按铁律 #6 留作惰性墓碑，无人读写。

上游：Event 执行收尾后被调用。EverMemOS 写入已迁到 `MemoryModule.after_turn()`。

## 2026-06-17 — LLM 调用切到 protocol-agnostic 的 get_helper_sdk()

PR #25 把 `_call_llm_for_update` 里的 `OpenAIAgentsSDK()` 直接实例化改成
`get_helper_sdk()`。与全仓 helper LLM 收敛一致（铁律 #9）：摘要生成用的 helper 不绑死
OpenAI Agents SDK，底层可换而本文件不动。`model` / `reasoning_effort` 仍取自
`narrative_config.NARRATIVE_LLM_UPDATE_*`，调用契约与更新逻辑均不变。

## 2026-07-07 — 后台 LLM 更新必须用 owner 的 Helper LLM，不再落到平台 key

`_async_llm_update` 是 `asyncio.create_task` 出去的**脱离任务**——它不继承
`AgentRuntime.run`（一个 async generator）在自身 ContextVar 上设的 per-turn helper 配置。
在此修复前它裸调 `get_helper_sdk()`，于是 `_ConfigProxy` 一路回退到全局 `_holder`
= 平台的 `settings.openai_api_key`。2026-07 事故：平台 OpenAI key 过期后，走 bus/jobs
的后台 narrative 更新全部 401，且被 `_call_llm_for_update` 的宽 `except → return None`
静默吞掉，长记忆退化约两周无人察觉。

两处改动：
1. `_async_llm_update` 开头调 `inject_owner_helper_credentials(narrative.agent_id, db)`
   （provider_resolver 里的共享原语，走完整 ProviderResolver：用户自配则用自配，免费额度
   用户走系统 provider）。解析失败（配额耗尽/无 provider）**不再落到平台 key**——跳过更新
   并 `alert_background_llm_failure`（service_audit 审计 + owner inbox 通知）。
2. `_call_llm_for_update` 不再吞异常 `return None`，异常上抛；`_async_llm_update` 对
   凭据类异常（`is_credential_error`）发告警，瞬时异常仍仅记日志。

## 2026-08-12 — 抽取开始看工具调用（缺陷 A1）

### 为什么

`_build_update_context` 原来只拼三段，第三段的「这一轮 agent 说了什么」取的是
`event.final_output`。**`event_log` 一行都没读。**

问题在于 `final_output` 是 agent 的**自述**，不是它**做了什么**。当这一轮的回复是
通过渠道工具送出去的（chat / Lark / Slack / bus），`final_output` 就退化成一句元评论
（"Good — I've already sent the findings"），这一轮真正产生的名词一个都不在里面。

而**检索面就是抽取产物** —— BM25 打分的文本正是这里生成的
`name + current_summary + topic_keywords`（加冻结的 description）。抽取时漏掉的东西，
下一轮检索就是零重叠；零重叠的 narrative 连候选池都进不去，judge 看都看不到。

全库实测（539 event）：212 个 event 调过 `send_message_to_user_directly`，其中 **83 个
（占全库 15.4%）的 `final_output` 不到 200 字符，40 个干脆是空的**。这 83 轮里，用户
真正收到的内容完整躺在 `event_log` 里没人读。

参照案例 `evt_04c2105c77c34d61`：13 次工具调用查遍了部署脚本和日志，结论（`deploy.sh` /
`update.sh` / `web.log` / `Errno 48` / 端口 1995）全在最后那个 tool_call 的参数里；
narrative 的 keywords 却是 `['帮我查一下部署脚本的报错']` —— 就是原句本身。用户下次问
"上次那个端口占用的问题呢"，与这条 narrative 的字面重叠是**零**。

### 怎么做

新增模块级纯函数 `build_action_digest(event_log) -> str`，`_build_update_context` 用它
拼第四段 `## Actions taken this turn`。**没有工具调用时返回空串，整段不输出**（40.1%
的轮次如此，不能留空标题）。

设计目标要说清楚：**让动作的"名词"进入 `topic_keywords`，不是让 LLM 复述工具输出。**

所有阈值都来自全库普查，不是拍脑袋 ——
`reference/self_notebook/data/eventlog_survey_2026-08-12.md`。几条**普查推翻了初版设计**
的地方，改之前先看这里：

- **`tool_output` 只留成败标记，不留正文**。初版设计说"取前 80 字"。普查发现话题名词
  实际落在偏移 738–7070 处，任何头部切片都够不到；而 `tool_output` 占全部 event_log
  字符的 32.4%。保留成败是因为"部署失败"和"部署成功"是不同的话题状态。
- **路径类参数留尾不留头**。`/Users/.../project/deploy/deploy.sh` 有 81 字符，头部截断到
  60 得到的是一串目录名，`deploy.sh` 没了 —— 路径的信息密度在尾部。
- **预算 2000 不是 1500**。渲染后 p95=1238 / p99=2171；1500 会系统性地截断"长工具链 +
  正文被挤进 tool_call"这一类，而那恰恰是 A1 的目标人群。
- **ID/控制类参数按键名丢**（`agent_id` 830 次、`max_results` 505 次…占参数实例的
  45.9%）。它们**都很短**（`agent_id` 只有 18 字符），任何长度阈值都拦不住。
- **凭据必须脱敏**。普查在 tool_call 参数里翻出**真的** Lark `app_secret`（12 个）、
  Telegram / Slack `bot_token`（5 个）、`app_token`（1 个）。传播链是：参数 → update
  prompt → `current_summary`/`topic_keywords` → 落库 → **此后每一轮的 system prompt**。
  做了两层：键名规则 + 值形态规则（后者防"token 被粘进 Bash 命令行"，本库目前 0 例，
  防的是形状不是已发生的事故）。

tool_call 与 tool_output **按位置配对**：全库 539 个 event 里两者数量恒等、恒严格交替，
而 `tool_output.tool_call_id` 只有 4/1550 有值 —— 那个 id 不可用于配对。

超预算时倒序保留最近的动作，块首标 `(N earlier steps omitted)`。**不静默截断** ——
LLM（和读日志的人）要能区分"agent 只做了三件事"和"我们只给你看了三件事"。

### 联动改动（不在本文件，但 A1 离了它就是空转）

`step_4_persist_results.py` §4.3 原来只把 `final_output` 回写内存 Event，
`ctx.event.event_log` 一直是创建时的 `[]`。也就是说 `build_action_digest(event.event_log)`
在生产上**永远拿到空列表**，而单元测试因为自己构造 Event 全绿。同 commit 补了那行回写，
细节见 `mirror/.../step_4_persist_results.py.md` 的 2026-08-12 条目。

**改这个文件时的连带检查**：`_build_update_context` 读的任何 `event.*` 字段，都要回去确认
§4.3 真的把它同步回内存对象了 —— 库里有不等于手上这个对象里有。
