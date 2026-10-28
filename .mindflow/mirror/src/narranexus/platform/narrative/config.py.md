---
code_file: src/narranexus/platform/narrative/config.py
last_verified: 2026-08-27
stub: false
---

## 2026-08-26 — `NARRATIVE_MERGED_ROUTING_ENABLED` + 合并 prompt 的输入预算

新开关(缺省 `0`)控制**路由结构**:开时 BM25 每轮先跑,然后要么零 LLM 快门,
要么一次合并调用;关时是今天的 continuity→judge 两次串行。

**注释里把回滚范围写准了**,这是吸取桶开关的教训(那条注释一度声称"翻回 True
即完整回滚",而 taxonomy prompt 已被单独删除、回不来,被 PR #361 review 抓到)。
这次:翻回 `0` **只**回滚路由结构,而且本批**没有别的东西需要回滚** —— 合并
prompt 常量关时无引用;共享渲染块对 continuity/judge 字节相同(有测试钉);
审计列纯增量、关时恒 NULL。

**与桶开关互斥,启动即 raise**(`_reject_untested_flag_combination`,模块导入
时执行)。理由不是洁癖:合并 prompt 的锚点位建立在"桶不是可续接锚点"
(`is_reusable_anchor`)这条性质上,两个都开则该保证消失,而**那个世界从未被
测量过** —— 所有臂、所有干跑、所有 prod 数字都是桶关着取的。启动失败的代价
是一次重启;静默的代价是一次没人能解释的路由判决,而且正好落在冻结锚点 +
身份夺舍那个形状上。

**输入预算五个常量**(`MERGED_*_MAX_CHARS` / `MERGED_PARTICIPANT_MAX_CANDIDATES`)
是**读侧**上限:只裁 prompt 看到的内容,不改任何存储字段,全部保头。为什么
两次调用时代不需要:那时每个 tier 只读字段的一个子集,而锚点块只在有锚点的
轮次渲染;合并 prompt 每一轮都渲染全套,于是"某个字段偶尔很长"从一个 tier 的
坏运气变成每一轮的延迟与账单。退役条件立单在
`todo/2026-08-26-merged-routing-flag-retirement-condition.md`。

## 2026-08-16 — NARRATIVE_DEFAULT_BUCKETS_ENABLED（C-1 default 桶治理）

八条播种叙事（GreetingAndCourtesy…）**不再是路由容器**。开关为 False（出厂值）
时它们退出 BM25 池、退出 judge 候选菜单、不再为新 (agent,user) 播种；八个类目名
**保留在 judge 的 instructions 里当词表**——"这一轮没有可沉淀的话题"这个判断仍然
用它们表达，只是不再指向一行可以把 event 塞进去的记录。

为什么必须动（实测，spec `2026-08-14-default-bucket-governance-design.md`）：
prod 用户轮 **26.4%**、prod 真实切片 chat 轮 **27.0%** 的主叙事是桶，而全库
**9080/9080** 条桶的摘要至今是出厂模板——桶永远不积累检索面，进去的话题**再也召
不回来**。另外这八行只是待在池子里就扰动了 **9.7%** 的 top-1（IDF/avgdl 按传入集
合算）。

开关默认 False = 新行为；置 True 整体回滚（同装置 before/after 对照用）。两种取值
下**都不删除任何存量行**（铁律 #6）——只是路由不再看见它们。
## 2026-08-14 — 撤回单位门：持锚点不创建是实测终案（supersede 下一条）

单位门（8 units）上线前实测被否：中文正常延续句 BM25 top-1 落在 1.0-3.2
（骑在 RAW_FLOOR=3.0 上），≥8 单位的纯确认句（「嗯嗯我明白了那就这样吧」）
probe 出来就是沉默——一段连贯 7 轮中文对话被打成 5 条 narrative（40 字符
门下同对话=1 条）。BM25 在 CJK 上无法区分「新话题」与「省略式延续」，
错误不对称定案：错归档可恢复（下一个 full turn continuity 重路由、
switch_narrative 在），碎片化不可恢复（永久分裂+agent 半途拿空历史）。
故 `FAST_NEW_THREAD_MIN_QUERY_UNITS` 删除，持 live 锚点时 fast 路径一律
不创建；完整数据与新线三来源写在 FAST_ANCHOR_OVERRIDE_FLOOR 的 NOTE。

## 2026-08-14 — 新线门改按语言单位计数（R2 复核 I3）

`FAST_NEW_THREAD_MIN_QUERY_CHARS`(40 字符) → `FAST_NEW_THREAD_MIN_QUERY_UNITS`
（默认 8，env `NARRATIVE_FAST_NEW_THREAD_MIN_QUERY_UNITS`）。字符计数对
CJK 全盲：中文完整句 11-15 字符，40 字符门对 zh 用户几乎永不打开——锚点
仍吞掉一切换题（正是要修的症状）。单位=CJK 每字 1（汉字/假名/谚文约各
承一词）+ 其余按空白分词每词 1（`narrative_service.query_units`），中英
同尺。残余偏置对所有语言一致声明：低于 N 单位的新话题句先留旧线程（如
6 词英文短命令），等更完整消息再开新线。默认值是临时校准，待
gate_top1_raw 数据落地后重调。

## 2026-08-14 — FAST_NEW_THREAD_MIN_QUERY_CHARS（#307 增量 🟡1）

fast 路径开新线的长度门（默认 40，env
`NARRATIVE_FAST_NEW_THREAD_MIN_QUERY_CHARS`）：锚点在手时只有「BM25 全面
沉默 + query 长到沉默可信」才 create。依据即本文件 RAW_FLOOR 注释里的实
测（<40 字符中位 top1 ~5.3）：短省略句零重叠属常态、不可当新话题证据；
完整句子零重叠=真新话题。已声明的残余偏置：短的新话题句会先留在旧线程，
等更完整的消息再开新线——一次错归档好过每个"ok"开一条线。不是时间窗。

## 2026-08-14 — FAST_ANCHOR_OVERRIDE_FLOOR（#307 🟡1/🟡2）

新阈值：fast 路径持有 live 锚点时 BM25 抢线所需的强分下限（默认 12.0，
env `NARRATIVE_FAST_ANCHOR_OVERRIDE_FLOOR`）。刻意高——raw BM25 随 query
长度伸缩（<40 字符中位 ~5.3），短跟进句永远抢不了线、留在原线程；长而
主题明确的消息可以切。与 RAW_FLOOR（噪声滤网）职责不同。同时在
2026-05-20 session 永不超时 NOTE 旁补注：fast 路径同守此规（曾短暂引入
30 分钟窗，同日删除）。

## 2026-07-29 — 高置信判据换成 RAW_FLOOR + MARGIN_RATIO

删除 `NARRATIVE_MATCH_HIGH_THRESHOLD = 0.70`。它比的是 squash 后的
`s/(s+1)`，等价于原始分 2.33，在中文单字 unigram 下几乎恒真——273 条真实
prod 轮次实测短路 87.5%。换成 `NARRATIVE_MATCH_RAW_FLOOR = 3.0` +
`NARRATIVE_MATCH_MARGIN_RATIO = 2.0`，同一批数据短路率降到 48.0%。

**RAW_FLOOR 是噪声过滤不是强度测试，别调高**——原始分随 query 长度涨，高
floor 会毙掉短追问。定值依据和取舍全写在 [[routing_gate.py]]。改这两个常量
会让 `tests/narrative/fixtures/routing_cases.json` 的期望值失效，必须一起重算。

> 2026-05-29：删除全部 `EVERMEMOS_*` 常量（EverMemOS 整体移除）。Narrative
> 检索现在无条件走本地 VectorStore，没有外部检索后端开关。

# config.py — Narrative 系统所有可调参数的中央控制台

## 2026-08-27(round 4)— 回滚契约补真话 + 退役条件内联(I3/M5)

**I3**:candidates_json 的 raw_score 精度随全深度排名(round 3 I1)对
**两臂同时**变化,且不受开关控制——回滚段落原来的"nothing else needs
undoing"是又一次"承诺了做不到的回滚"(桶开关注释犯过的同一课)。现在
第四条 bullet 写明:审计序列在部署日有台阶,跨部署窗口的"多少候选得分"
类分析必须按部署时间切窗(读法同 SHADOW_POOL_RECORD 的 ⚠ 注)。
.env.example 同句同步。**M5**:退役四条件(考卷过/prod 开 14 天未回滚/
五种 verdict+至少一次 fallback 走过真机/Owner 决议老路不复活)从
"指向本地工单的指针"改为内联——决定 flag 何时可删的事实必须仓内可读。

## 2026-08-27(round 3)— NARRATIVE_POOL_LIMIT 归位通用检索段 + env 化

它是**所有路径**上 load_pool 的取数上限,不是合并旋钮——原先放在合并段,
调它的人会以为只影响 merged 臂(M3)。排名现在恒为全池深度(I1),
所以它不再有需要同步的孪生常量;补 env 覆盖与邻居一致。

## 2026-08-27(round 2)— NARRATIVE_POOL_LIMIT(I5)

`load_pool` 的 fetch 上限与合并路径的全量排名深度曾是两处字面量 100,
靠一句注释维系相等——池扩容而排名不跟,`anchor_bm25_rank` 会在 100 处
静默截断,而它的 NULL 被定义为"锚点零分"。合一为一个常量,默认值原样。

## 2026-08-27 — MERGED_* 预算走 _env + 组合禁令的可读报错(review Minor 7/9)

六个合并 prompt 预算(prev_response/anchor_summary/awareness/query/
participant/menu_size)从字面量改为 `int(_env("NARRATIVE_MERGED_...",
默认))`——灰度期按实测 misroute 调参改 deploy 侧 .env 即可,默认值不变
纯加法;.env.example 注释列全。组合禁令(buckets×merged 同开拒绝启动)
**留在 import 期**:审查提议挪到各进程 preflight,但漏接一个入口 = 该
进程闸门消失,比 import 链 traceback 更糟——折中为 raise 前先
`logger.critical` 打一行人话(ops 真正会看到的那行),偏离处方已记录。

## 2026-08-26 — `NARRATIVE_SHADOW_POOL_RECORD`(切片 0 仪器开关,缺省开)

续接轮要不要也把 BM25 池打分记下来。**只控记录,不控决策** ——
翻它不改变任何一轮的落点。

**为什么给它开关**:理由不是延迟(两次 DB 读+一次快照去重 SELECT(稳态 ~1 INSERT)~13.5ms,外加影子行 candidates_json 带整池(10KB 级)的表容量增长——对着这条路径 p50 8.5 秒的
setup 阶段可以忽略),而是**回滚粒度**。这一批每一个同类治理开关都是 env 门控的
(下面的 `NARRATIVE_DEFAULT_BUCKETS_ENABLED` 就是),没有开关意味着关掉仪器要改
代码 + 重新发布两种运行模式(铁律 #7)。

**回滚**:`NARRATIVE_SHADOW_POOL_RECORD=0` + 重启。
**人群口径(2026-08-26 补)**:开着的稳态下后台触发续接轮也恒为
`pool_is_shadow=0`(仪器只记用户聊天轮),分析须叠 `is_user_chat=1`;
in-code ⚠ 注释同步了这一句。

⚠ **关闭态在数据上不可区分**:关掉后 `pool_is_shadow` 恒为 0,这与"续接轮从来
没有池"是同一个形状。于是那段窗口读起来会是"快门在续接轮上没有可释放人群",
而不是"我们那段时间没在看"。**要关就把窗口记下来,表事后告诉不了你。**


## 2026-08-20 — 新增 `DESCRIPTION_MAX_LENGTH = 512`

在 `NarrativeCRUD.create` 里给 `narrative_info.description` 封顶。

**为什么是 512 而不是 `SUMMARY_MAX_LENGTH`(200)**:八条 default 桶的
description 是人工撰写的、会进 judge 的候选清单,而 `GreetingAndCourtesy`
是 **206 字符** —— 200 会静默截断被 P1 冻结的 prompt 内容。
512 越过每一条桶,同时仍然只夹住病理长尾(prod 非 default:55% 在 200 字以内,
21% 超过 1500,max 198,398)。
"以后有人把这两个常数对齐"这件事由测试钉住
(`test_the_bound_does_not_clip_a_curated_default_bucket`)。


## 为什么存在

Narrative 检索、连续性判断、embedding 更新是计算密集型操作，各阶段的阈值直接影响系统的记忆质量和 API 成本。把所有参数集中在一个单例对象里，有几个好处：实验调参时只需改一处；文档注释就在代码旁边，解释每个参数的含义和推荐范围；生产与开发环境可以通过替换此对象的字段来切换行为，不需要散落在各处的 if/else。

## 上下游关系

**被谁用**：`_narrative_impl/retrieval.py` 读 `NARRATIVE_MATCH_HIGH_THRESHOLD`、`NARRATIVE_SEARCH_TOP_K`、`EVERMEMOS_*` 系列参数；`_narrative_impl/updater.py` 读 `NARRATIVE_LLM_UPDATE_MODEL`、`EMBEDDING_UPDATE_INTERVAL`；`_narrative_impl/continuity.py` 读 `CONTINUITY_LLM_MODEL`；`_event_impl/processor.py` 读 `MAX_RECENT_EVENTS`、`MAX_RELEVANT_EVENTS`；`session_service.py` 读 `SESSION_TIMEOUT`。

**依赖谁**：无外部依赖，纯 Python 类。文件末尾导出单例 `config = NarrativeConfig()`，调用方通过 `from .config import config` 获取。

## 设计决策

所有参数都有行内注释解释推荐值、调参建议和适用场景，这是刻意的——这个文件就是系统的"调参手册"，不依赖外部文档。

`NARRATIVE_LLM_UPDATE_INTERVAL` 2026-07-24 起就在这里（NarrativeConfig 类属性）——包根那个只剩单常量的全局 config.py 已删除，narrative 是它唯一的消费者，「全局/局部」的历史分工不复存在。

`EVERMEMOS_ENABLED = False` 现在是默认值——云端部署目前没有运行 EverMemOS 服务，开着会让 backend 在每次 hook 写入时打 ConnectError。打开前先确保 EverMemOS 服务已经跑起来；retrieval.py 在禁用时直接走纯向量检索路径，不会触碰 HTTP 客户端。配套的 belt-and-suspenders 在 `utils/evermemos/client.py:get_evermemos_client` 里——禁用时返回 no-op stub，覆盖那些没显式 gate 的调用方。

`ENABLE_HIERARCHICAL_STRUCTURE = False` 和 `ENABLE_AUTO_SPLIT = False` 是 Phase 2 预留的功能开关，目前代码里没有对应实现，改成 True 没有效果。

## Gotcha / 边界情况

`VECTOR_SEARCH_MIN_SCORE = 0.0` 意味着向量搜索没有最低分过滤，所有 Narrative 都会进入候选集再由 LLM judge 裁决。这是有意设计的，用宽松召回 + 精准 LLM 判断替代严格阈值过滤，但候选集如果很大（几百条 Narrative），LLM judge 的 prompt 会很长。如果发现 LLM judge 超出 token 限制，可以调高这个值做初步过滤。

`EMBEDDING_MODEL = "text-embedding-3-small"` 修改后需要重新生成所有历史 embedding，因为新旧模型的向量空间不兼容，直接混用会导致语义检索结果完全错乱。有专门的 `EmbeddingMigrationService` 处理这种迁移，但需要手动触发。

## 新人易踩的坑

`MAX_NARRATIVES_IN_CONTEXT = 3` 控制的是 select() 返回的 Narrative **数量上限**，不是每条 Narrative 注入多少事件。事件数量上限由 `MAX_EVENTS_IN_CONTEXT = 6` 控制，这两个数字容易混淆。
