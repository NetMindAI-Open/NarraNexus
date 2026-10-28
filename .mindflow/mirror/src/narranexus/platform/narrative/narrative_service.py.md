---
code_file: src/narranexus/platform/narrative/narrative_service.py
last_verified: 2026-08-27
stub: false
---

# narrative_service.py — Narrative 统一门面

## 2026-08-27(round 7)— 委托改传三个协作者

`_select_merged` 薄委托现在传 `self._crud, self._retrieval,
self._write_audit`——impl 的上行私有依赖清零(round 5 收到 3 个,本轮
连那 3 个也改为显式参数)。

## 2026-08-27(round 5)— 上行私有依赖收敛到三个(I3)

merged_select 对 service 私有成员的语义依赖从 5 个收到 3 个
(_crud/_retrieval/_write_audit,审计写入留在 service 合理):
`_land_no_topic_turn` 本体迁往 [[landings]](service 留同签名薄委托),
`_advance_session_anchor` 迁往 [[anchor_rules]]。若再整理本文件私有成员,
先看这两个 impl 模块的消费面。

## 2026-08-27 — 合并路径编排搬出(review Important 2)+ Landing 化

`_select_merged` 的 255 行编排整体迁入
`_narrative_impl/merged_select.py`,本文件只留薄委托;五个 verdict 落点
与 `_land_no_topic_turn` 统一返回 frozen `Landing`(6 字段一次构造,
新 result 字段漏赋在构造点炸)。`is_reusable_anchor` / `_minutes_since`
定义下沉到 `_narrative_impl/anchor_rules.py`(impl 不得上行 import),
本文件 re-export `is_reusable_anchor` 保住 step_1_fast_select 的公共
import 面——**仍是唯一定义**。select() 的 no-topic 落点只取 Landing 的
四个决定字段,flag-off 字节路径不变。(round 2 补:continuity 计时的
LLM 型号打标改走 `agent_framework.llm.call_tagging`,三份拷贝收敛。)

## 2026-08-26 — 影子记录收窄到用户聊天轮(PR #365 review M4,刻意决定)

后台触发(job/message_bus/IM webhook)按设计无会话锚点,其影子行对
"快门在用户对话上的可释放人群"恒无贡献(bypass_reason 必为
background_scope),却照付记录成本——message_bus 单独占 dev 轮次 ~30%。
守卫改为 `NARRATIVE_SHADOW_POOL_RECORD and is_user_chat`,测试
test_a_background_continuation_turn_is_not_recorded 钉住。这是范围选择
而非遗漏;若将来要拿后台轮当"continuity 误判"的证据面,重新放开时
须同步此条与该测试。**连带语义(收窄的连带面)**:收窄后
`pool_is_shadow = 0` 一侧同时装着"决策行"和"后台续接轮"两种行,
人群判别从单列变为 **`pool_is_shadow` + `is_user_chat` 两列共同**;
全体续接轮上的覆盖率按设计明显低于 100%,真实比例用
`GROUP BY is_user_chat` 查(后台轮占全部轮次 ~30% 是实测,占续接轮的
比例未测),别对固定数字比。
`_record_shadow_pool` 链上的 `is_user_chat` 参数现恒为 True
(调用点守卫过),保留是为将来放开后台轮只改守卫一处。

## 2026-08-26 — 合并调用:一次判决取代 continuity+judge(开关缺省关)

`select()` 顶部多一条**提前 return**:`NARRATIVE_MERGED_ROUTING_ENABLED` 开时
走 `_select_merged`。刻意是提前 return 而不是把分支织进方法体 —— 它下面的
所有代码就是今天的两次调用路径**原样**,于是"开关关 = 今天的行为"是一条
可以读出来的性质,不是一句要人相信的话(测试 `test_with_the_switch_off_the_
two_call_path_is_untouched` 连 `build_merged_prompt` 都设成地雷)。

**为什么值得做**(spec `2026-08-25-merged-routing-design.md` §4):prod 7 天
真人轮 n=189 里 43 轮付两次调用,串行 p50 8,924ms / 均值 13,004ms,而整个
非 LLM 检索层均值 47.6ms。唯一有分量的杠杆是往返次数。**"检索花 6 秒"是
误归因**(`retrieve_ms` 包含 `judge_ms`,被相加了两遍),别再去修它。

`_select_merged` 的形状:
1. BM25 每轮先跑(`prepare_merged_routing`,~14ms 本地)
2. **零 LLM 快门** = `evaluate_bypass` 的 `anchor_match` 判决挪到 continuity
   之前,判据一个字没改 → `selection_method="anchor_confirmed"`
3. 否则**一次**合并调用,五个出口(continue_anchor / match / participant /
   new / no_topic)
4. **落点全是既有执行器**:锚点直接返回 / `assemble_match_landing`(judge 自己
   的 search 落点)/ `create_from_query` / `_land_no_topic_turn`。换决策者,
   不换执行者 —— 所以 step_1、step_4、ChatModule 完全不知道上游变了
   (`test_downstream_cannot_tell_who_decided`)

**兜底是这里最不能动的地方**(rule 6):合并调用异常 / 超时 / verdict 不在契约
内 / index 越界 → **有可续接锚点就留在锚点**(`merged_fallback_anchor`),
无锚点才允许新建(`merged_fallback_new`,同样打标)。D19 两记实锤都是
"决策层失败→掉进新建→新线成为锚点→updater 逐轮改写它的身份"这个形状,
所以"失败就当新话题"这条捷径永久封死。

`_advance_session_anchor` 从 `select()` 抽成静态方法,两条路共用:锚点推进
规则以两份字面量存在,正是快慢两路当初就同一条不变量打架的原因(独立审查
2026-08-21 Important #3),第二个决策者就是第二次机会。

## 2026-08-25 — 续接轮也记池(切片 0),但判决一个字不改

continuity 短路那条分支里,原本只 new 一个空的 `RoutingAudit`;现在多 await 一次
`_record_shadow_pool`。**判决在此之前就已经定死**(`narratives` /
`selection_method` / `chosen_narrative_id` 都已赋值),记录器碰不到它们。

**不变性由测试钉死**:`test_shadow_pool_record.py` 里那条
`test_the_verdict_is_byte_identical_with_and_without_the_recorder` 把记录器
monkeypatch 掉再跑同一轮,逐字段比对决策列。**一个会改变被测对象的仪器不如没有**,
所以这条断言是这个文件存在的理由,不是附加项。

**失败边界是一个具名的窄口子**:`_record_shadow_pool` 里的 try/except 只包住记录器
本身,决策路径的异常照常往上抛。

**except 里只剩一行 log**(2026-08-26 review 收口):原先那 8 行"把 audit 重置回
切片 0 之前的形状"是一份手抄的字段清单,写下来的当天就漏了 `gate_reason`,而且
根本没回滚快照。现在 `record_pool_only` 是原子的 —— 会失败的活全部先算完,最后
在一段不可能抛异常的赋值块里一次提交 —— 所以没有清单可漂。

**开关**:`config.NARRATIVE_SHADOW_POOL_RECORD`(env,缺省开)。加它的理由不是
延迟(~13.5ms+快照去重 SELECT,对着这条路径 p50 8.5 秒的 setup 阶段可以忽略;
真实代价是容量——影子行 candidates_json 带整池,10KB 级/行),而是**回滚粒度**:
这一批每一个同类治理开关都是 env 门控的,没有开关就意味着关掉仪器要改代码 +
重新发布两种运行模式(铁律 #7)。⚠ 关闭态下、**以及开启态的后台触发轮上**,`pool_is_shadow` 恒为 0,前者与
"续接轮从来没有池"**在数据上不可区分** —— 关了要记下窗口,表事后告诉不了你;
后者是设计范围(user-chat only),查询须叠 `is_user_chat = 1` 区分。

**`session` 在这里不是 Optional**:进入这条分支要求 `narratives` 非空,而它唯一的
赋值处被 `if is_continuous and session and session.current_narrative_id` 守着。
原先的 `if session else None` 是一条永远走不到的分支,而缺失的类型标注正好掩盖了
这一点。(`_land_no_topic_turn` 里那个形似的守卫是**真的** —— 那条路径确实可能
没有 session。)


## 2026-08-20 — 会话锚点开始参与免审决策(Q 层)

`select()` 现在把 `session.current_narrative_id` 与 `is_user_chat` 一起传进
`retrieve_top_k`。**为什么在这一层读锚点**:Session 的所有权在这里,
下面的检索层不该知道什么是 Session(那会把一个纯排序层绑到会话模型上)。

调用点顺序上有一个必须记住的事实:走到 `retrieve_top_k` 时
`is_continuous` 一定是 False(否则上面就 return 了)。所以"锚点存在但
continuity 说不连续"是**结构性的**,不是两层打架 —— 引用审计数字时别把它
读成缺陷,那是选择偏差。

`is_user_chat=False` 的分支与本文件末尾那段"只有用户发起的轮次才写
`current_narrative_id`"是**同一个设计的两端**:后台 trigger 没有锚点,
所以锚点规则对它们不适用。改任何一端都要同时看另一端。

## 2026-08-16 — 无持久话题的落点：anchor-first（C-1 方案 ④-A′）

judge 回答的是关于**这一轮**的问题（"这里有没有值得单独成线的东西"），它**不给
目的地**。目的地由新的 `_land_no_topic_turn` 决定，规则与 `step_1_fast_select`
早已定稿的形状**刻意保持一致**，避免快慢两条路对"没内容的一轮该去哪"产生分歧：

1. **有真实线锚点 → 复用，且不碰它的检索面**。任务中间的一句"你好"属于那个任务
   （标注协议 R1），用户也指望 agent 还记得在干什么。关键在"不碰"：
   `NARRATIVE_LLM_UPDATE_INTERVAL=1`，让 updater 跑起来意味着每句寒暄花一次
   helper 调用，并且 name/summary/keywords 是**全量覆盖**——一句"你好"足以把工作
   线改名。
2. **无锚点 + durable → 建线**。聊天历史端点是按 narrative 取的、ChatModule
   instance 挂在 narrative 上，这里裸跑会让首次接触的一轮**从用户自己的历史里消
   失**。新建的线不是垃圾：它成为锚点，随着真实话题浮现由 updater 改名。
3. **无锚点 + ephemeral（voice/F28）→ 裸跑**。刻意为之：不留痕迹，好让下一条打字
   消息的连续性判定"就像这轮语音没发生过"。

三种落点在审计里可分辨（`no_topic_anchored` / `new_created` / `no_topic_bare`），
因为本批次最大的风险是碎片化，而只有 `new_created` 那一支会真的多出一条线。

## 2026-08-21 — `is_reusable_anchor()`：锚点复用判定收敛为一份定义

独立审查（Important #3）发现"锚点是不是可复用的线"这一判断以字面量形式散在两处
（`select()` 连续性守卫、`_land_no_topic_turn`），而快路径 `step_1_fast_select`
根本没有这个检查——慢路径每轮把桶锚 session 推出桶、快路径每轮把它钉回去，两条
路互相拆台（C-1 上线时 26.4% 的 prod 用户轮以桶为主叙事，这批 session 真实存在）。
现在收敛为模块级 `is_reusable_anchor(narrative)`，三个读点共用；语义带回滚开关：
`NARRATIVE_DEFAULT_BUCKETS_ENABLED=True` 时桶重新成为可复用容器（旧世界语义）。

## 2026-08-16 — 连续性不得锁在 default 桶上（C-1 方案 ⑤）

`select()` 在跑连续性检测**之前**先看锚点：锚点是 `is_special == "default"` 的行
就直接不跑这一层（不是跑完再忽略——那是一次白花的 helper 往返）。桶是**对某一轮
的判断**，不是一条线，没有"延续"可言。实测：重演 155 个落桶轮里 **59 个**是被连
续性按在桶里的，最长连锁 11 轮，而这些轮次全都不可召回。

与 C-2 那条 prompt 改动（`prompts.py`）配套：两者合起来才截断闭环
`停进桶 → 容器规则判 False → 重走检索 → 冻结模板 raw=0 → 又落回桶`。
## 2026-08-14 — 撤回 query_units/新线旗标（supersede 下一条）

`query_units` 与 `FastSelectResult.related/suggests_new_thread` 删除
（单位门实测在中文上复发碎片化，见 config.py.md 同日条目）。终版契约：
`FastSelectResult{narrative, top1_raw}`——命中与分数，仲裁全在 step 层，
分数进 audit（`gate_top1_raw` 落库锁保留）。

## 2026-08-14 — query_units + audit 落库锁（R2 复核 I2/I3）

新增模块级纯函数 `query_units(text)`：脚本无关的 query 体量（CJK 每字 1
单位 + 其余每空白词 1 单位），select_fast 的新线门改用它（字符数对 CJK
全盲）。`audit_fast` 的 `top1_raw→gate_top1_raw` 映射补了**落库断言**
（test_fast_path_service.test_audit_fast_persists_top1_raw，删映射即红，
mutation 验证过）——`_write_audit` 按设计吞掉一切异常，对象层断言抓不住
repository 映射回归，只有查回持久化行才算锁住。

## 2026-08-14 — select_fast 返回 FastSelectResult（#307 增量 🟡1/🟡2）

契约从 Narrative-or-None 升级为 frozen dataclass `FastSelectResult`
（进程内值对象不上 wire，故不用 pydantic）：`narrative`=当前 floor 下的
决断命中；`related`=top-1 过噪声底；`suggests_new_thread`=沉默可信（无
related 且 query ≥ config.FAST_NEW_THREAD_MIN_QUERY_CHARS）；`top1_raw`
随行给 audit。`audit_fast` 增 `top1_raw` 参数写既有 `gate_top1_raw` 列
（不动表、无双方言面）。三个测试消费方（test_select_fast /
test_fast_path_service / test_step_1_fast_select）同批更新。

## 2026-08-14 — select_fast 双 floor + audit_fast（#307 🟡1/🟡4）

`select_fast` 增 `against_live_anchor: bool = False`：调用方持有 live
session 锚点时命中意味着**抢线**，用 `config.FAST_ANCHOR_OVERRIDE_FLOOR`
（强分阈值）而非噪声 floor。新增 `audit_fast(...)`：快路径每个路由决策
（命中/复用/新建/裸跑）落一行 RoutingAudit（selection_method="fast"，
continuity/judge 字段保持「该 tier 未跑」的 None 语义，不填 0/假值），
委托既有 `_write_audit` best-effort——快路径与 full select() 同享 DB 证
据契约。

## 2026-08-14 — create_fast：快路径的 CRUD-only 创建

`create_fast(agent_id, user_id, query)` 委托 retrieval impl 的
`create_from_query`（原私有 `_create_narrative` 公开更名），新 narrative
带与 full select() 创建完全相同的 BM25 路由面（title/keywords/topic_hint）。
select_fast 文档同步更新：miss 后怎么办是 surface 的事（voice 裸跑、
durable chat 落到 create_fast），continuity/LLM tier 仍是 full select() 独占。


## 2026-08-14 — `continuity_ms` / `retrieve_ms` 落审计行

`[TIMED] narrative.*` 一直在量这些，但只进 loguru：会轮转、没法聚合（教训 #5）。而 `[turn-timing]` 的 `setup_s` 只能说"setup 是一轮里最大的一块"，说不出是里面
**哪一层**。

四列的价值不在"叙事选择有多慢"，在**把成本和它买到的那个决策连起来**：短路的决策 vs
叫了 judge 的决策，各自花多少。所以 `None` 表示**这一层没跑**，绝不用 0——短路的决策
根本没调 judge，存 0 会把"仲裁有多贵"这个查询拽向零，恰好毁掉这几列存在的理由。

实测（本地真机）：`continuity_detect` 均值 3941ms、`llm_unified_match` 4690ms，两者
串行相加 ≈ 观测到的 setup p50 8.49s。**这两次 LLM 是串行的**——continuity 命中才跳过
retrieve。要真正压 setup 只有三条路（投机并发跑 retrieve、放宽 gate 阈值少调一次 LLM、
缓存 confirm），前者会在 continuity 命中时白烧一次用户的 LLM 调用，后两者被 PRD 明确
划在范围外。此处只记录事实，不擅自决定。
## 2026-08-07 — select() 现在落一行路由审计（E1）

`select()` 的决策证据以前只进 `ProgressMessage` 和 loguru，数据库里一个字节都没有
（事故教训 #5：日志会轮转、grep 只找得到你想到要搜的词）。于是「路由准确率提升了
X%」没有分母。现在每次 `select()` 结束写一行 [[narrative_routing_audit_repository.py]]。

两条分支都要写，这是重点：

- **检索分支**：审计由 [[retrieval.py]] 的 `retrieve_top_k` 组装（池子 + 闸门 +
  judge），挂在 `NarrativeSelectionResult.audit` 上带回来。
- **连续性分支**：没有池子也没有闸门，但**恰恰最需要留痕**——一次误判的
  `is_continuous` 会直接复用 `session.current_narrative_id` 且不做任何话题校验，
  而每轮又把这个 id 原样写回，一次误判能锁住若干轮。以前完全无记录，所以真实
  误判率至今未知。这里单独构造一个 RoutingAudit。

顺带把两个一直被算出来又扔掉的量存下来：`ContinuityResult.confidence`（`select()`
只取 `is_continuous`）和闸门的 `raw_score`（`NarrativeSelectionResult.scores` 只带
squash 后的值）。

新增 `trigger` 参数：dev 实测 chat 占 69%、message_bus 占 30%，而只有面向人的来源
会移动 session 锚点（`_is_user_chat`）。不分开记，两种行为会被平均成一个无意义的比率。

`_write_audit` **同步内联**执行，不是 `create_task`：两条小查询而已，而在这里
fire-and-forget 正是让 narrative 摘要静默失败两周的那个坑的翻版（无人 await 的
Task，异常只在 GC 时以 warning 出现——事故教训 #2）。若将来它出现在 step.1 的耗时
里，做写入合并，**不要改成脱离任务**。


## 2026-08-06 — auto review 收口（PR #247 两轮意见）

review 收口：select_fast 改走公开 keyword_search（私有名转正，service 不再下探 impl 私有面）并加 NARRATIVE_MATCH_RAW_FLOOR 分数门槛——低于门槛按 miss 裸跑，一词偶合不再成为背景 narrative。

## 2026-08-06 — voice fast mode: narrative 快路径（BM25 直取）

新增 public `select_fast(agent_id, user_id, query)`：BM25 top-1 直取（retrieval 公开面 keyword_search top_k=1 + CRUD load），零 LLM / 零新建 / 零 session 写；fast 模式（F28）唯一入口，select() 仍是唯一可新建/走 LLM 层的路径。

## 2026-07-28 — R4a：prompt 生成面扩为稳定/易变两半

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

`combine_main_narrative_prompt` 新增 `include_volatile: bool = True`（False =
稳定版模板，relocation 开启时 context_runtime 用它建 system prompt Part 1）；
新增 `combine_narrative_turn_prompt(narrative)`（每轮易变的
updated_at/current_summary 块，进当前轮消息的 [Turn context]）。均为
[[prompt_builder.py]] 的纯透传（与原 combine_main_narrative_prompt 同型）。

## 2026-06-01 — embed a clean retrieval anchor, not the execution prompt

`select()` gained a `retrieval_anchor` param. `resolve_retrieval_text(anchor,
input_content)` (module-level) picks `anchor` when a trigger supplied one
(`[From <name>] <body>`), else falls back to raw `input_content`. The resulting
`query_text` is what gets embedded (query vector), passed to continuity
detection, handed to `retrieve_top_k(query=…)`, and stored as
`session.last_query`. Motivation: IM/bus triggers were embedding the full
execution prompt (history + sender profile + instruction boilerplate), which
diluted the query vector and, for the bus, blew past the embedding token limit
(the only real 400 source in prod). Anchors are produced per-trigger and carried
via `trigger_extra_data["retrieval_anchor"]` → `ctx.trigger_extra_data` →
`step_1_select_narrative`. See the 2026-06-01 embedding-anchor design doc.

## 2026-05-20 — continuity keys off the last *visible* message (query OR response)

`select()`'s continuity gate changed from `if session.last_query:` to
`if session.last_query or session.last_response:`. Reason: when the agent
messages the user proactively (e.g. from a scheduled job), [[step_4_persist_results.py]]
anchors `last_response` + `current_narrative_id` but `last_query` is empty
(no preceding user query). The old gate skipped continuity entirely in that
case, so the user's short reply ("好") fell through to vector retrieval and
mis-routed. Now continuity runs whenever there is any prior visible exchange,
and [[continuity.py]] frames the proactive case as "the user is replying to a
message the agent sent". `is_continuous=True` still reuses
`session.current_narrative_id` (unchanged) — that anchor is now the chat-box
anchor, so it's the narrative the user is actually looking at.

## 2026-05-19 — select() 新增 `is_user_chat` 参数

修复 short-reply 后连续性判断崩的 bug。Session 的 `last_query` /
`last_response` / `current_narrative_id` 三个字段必须只反映**真实用户**的对话
轨迹，不能被 cron job / message_bus / lark / callback 这类 background trigger
覆盖。否则用户隔几分钟回复一个 "要" 时，`ContinuityDetector` 拿到的
`previous_query` 是 background trigger 的输入（cron payload / bus 消息），
连续性必然 False → 掉到 Top-K embedding 匹配 → 短消息 embedding 信息量极低 →
匹配错 Narrative 甚至新建。

- 新参数 `is_user_chat: bool = True`，由调用方根据 `ContextData.working_source`
  传入（`working_source == "chat"` → True，其它 → False）。
- `is_user_chat=False` 时整个 Session 更新分支被跳过，连续性判断仍然走，
  但 Phase 2 检索后**不**把 background trigger 的 query 写回 Session。
- 配套：[[step_4_persist_results.py]] 4.5 也加了同样的 source 判断，
  确保 `last_response` 同样只在 chat run 时被覆盖。


## 为什么存在

AgentRuntime 在编排流水线时不应该知道"向量检索是怎么做的"或"embedding 是什么时候更新的"。`NarrativeService` 就是这层隔离：它把七八个私有实现类（`NarrativeCRUD`、`NarrativeRetrieval`、`NarrativeUpdater`、`InstanceHandler`、`PromptBuilder`、`ContinuityDetector`）统一包装成四类公开操作——select、update、CRUD、instance management。

## 上下游关系

**被谁用**：`agent_runtime/_agent_runtime_steps/step_1_select_narrative.py` 调 `select()`；`step_5_update_narrative.py` 调 `update_with_event()`；`services/module_poller.py` 的 `InstanceHandler` 通过 narrative 包直接访问（不经过 Service 层）；`backend/routes/` 偶尔调 CRUD 接口给前端查询。

**依赖谁**：构造时立即实例化 `NarrativeCRUD`、`NarrativeRetrieval`、`NarrativeUpdater`、`InstanceHandler`；`set_event_service()` 注入 `EventService`（懒注入，`EventService` 构造时不需要）；`_get_continuity_detector()` 懒加载 `ContinuityDetector`（避免在不需要的路径下支付 OpenAI SDK 初始化成本）。

## 设计决策

`select()` 的逻辑分两条路：如果 `ContinuityDetector` 判断当前 query 属于 session 里记录的那条 Narrative（连续性为真），就**直接返回那条活跃主 Narrative**（2026-06-04 起：embedding 退役后不再做「补充 Top-K 候选」的向量检索）；如果连续性为假或没有 session，则走 `NarrativeRetrieval.retrieve_top_k()`——**纯 BM25 关键词检索**（name+summary+topic_keywords），低置信度时再由 LLM unified-match 仲裁。整条路径零向量、零 EverMemOS。主 Narrative 强制排在第一位这个设计是有意的，确保 AgentRuntime 的 step_2 在 contextruntime 组装时总能优先渲染主线 events。

`update_with_event()` 有两个重要 flag：`is_main_narrative` 控制是否做完整的 LLM 动态更新（更新 name、current_summary、topic_keywords），`is_default_narrative` 控制是否只加 event_id 而跳过一切其他更新（default Narrative 是全局共享的兜底分类，不允许被某一次对话"污染"摘要）。

曾经考虑过把 `EventService` 在 `__init__` 时必须传入，但这会导致两个 Service 的构造产生顺序依赖，最终选择了 `set_event_service()` 的依赖注入模式。

## Gotcha / 边界情况

`_updater.set_vector_store(self._retrieval.vector_store)` 曾经是为了让 `_retrieval` 和 `_updater` 共享同一个 `VectorStore`，保证 embedding 更新后检索侧立刻可见。**2026-06-04 起这层耦合已失效**：随统一记忆改造，narrative 路由改为 BM25（name+summary+keywords 词法匹配），`_updater` 不再维护任何 embedding/VectorStore，`check_and_update_embedding` / `force_update_embedding` / `_async_embedding_update` 全部删除。这行 `set_vector_store` 目前是无害的遗留物，待读路径向量代码一并清理时移除——不再有"别删掉"的理由。

连续性检测失败（LLM 报错）会静默 fallback 到"不连续"，不会抛出异常。这意味着偶发的 LLM 调用超时不会影响主流程，但会导致该轮对话建出一个新 Narrative，引起记忆碎片化。高并发下值得监控 `"Continuity detection failed"` 日志。

## 新人易踩的坑

`select()` 返回 `NarrativeSelectionResult`，不是 `List[Narrative]`——新代码如果直接当列表用会报属性错误。正确用法是 `result.narratives[0]` 取主 Narrative。

`session` 参数是**可变引用**：`select()` 内部会直接修改 `session.current_narrative_id`、`session.last_query` 等字段，调用方必须在 `select()` 之后再调用 `session_service.save_session(session)` 来持久化，否则下一次请求看到的 session 还是旧状态。

## 2026-08-16 — `_land_no_topic_turn` 的建线分支签名修正

`create_from_query` 的真实签名是
`(query, user_id, agent_id, narrative_type)`，`narrative_type` 必填。
`_land_no_topic_turn` 漏了它 → 每一个走到"无锚点 + durable → 建线"的真实轮次
都 `TypeError`。真机 2026-08-16 崩在这里。

**为什么单测没抓到**：fixture 手写了一个三参数的 `create_from_query` double，
**把错的调用形状固化成了"契约"**。现已改为
`create_autospec(NarrativeRetrieval, instance=True).create_from_query`——
签名由真实方法强制，调用错了测试就红。

同一次修正还发现 `service_no_topic` fixture 没 stub 连续性检测器，导致这套
单测**在真打 helper LLM**（110 秒 → 0.09 秒）。单测不得依赖供应商。

一般教训（与 step_4 那条"内存对象撒谎"同源）：**stub 掉的边界就是 bug 的
藏身处**。只 stub 你不拥有的东西（网络、时钟、DB），永远不要 stub 正在被测的
那段逻辑；double 的签名要从真实符号推导，不要手写。
