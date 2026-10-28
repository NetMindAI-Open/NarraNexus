---
code_file: src/narranexus/platform/narrative/_narrative_impl/prompts.py
last_verified: 2026-08-27
stub: false
---

## 2026-08-27 — 合并指令改为按轮拼装(review Critical 1)

独立审查抓到一个必然自伤的组合:共享核心里"遗留容器也能续"的段落 +
答案表把 continue_anchor 写死为 DEFAULT,而 `_contract_violation` 对
"锚点不可续 + continue_anchor" 一律拒绝——模型顺着 prompt 答,答案必被
拒,落点是 merged_fallback_new(正是文件头严防的 D19 形状)。修法:
删掉那段"continues even there"(routing_blocks 删 `_LEGACY_BUCKET_NOTE`
用的同一个理由,这次用在自己身上);两份字面量指令换成
`build_merged_instructions(anchor_is_continuable, with_participants)` ——
答案表、priority 列表、输出格式的 verdict 清单全部由同一次选择拼出,
与 `merged_router.allowed_verdicts` 同源,**prompt 说有的 = 契约收的**。
2×2 变体全走拼接不落字面量;测试从"双常量循环"升级为"四组合对 builder
循环"+"每变体 offered==accepted"钉。遗留容器规则活在
`ANCHOR_NOT_CONTINUABLE_NOTE`(恰在适用的轮渲染),不再进共享核心。

## 2026-08-26 — 合并路由的 prompt(第一天就上配对纪律)

新增 `_MERGED_ROUTING_CORE`(共享核心)+ 主版 / participant 版指令,
`_NO_DURABLE_TOPIC_RUBRIC` **原样**splice 进两份。(08-27 起两份字面量
已被上一条的 builder 取代,共享核心与判据不变。)

**为什么一出生就是共享常量 + 变体循环断言**:participant 那一对已经静默
分叉三次(最后一次 PR #361 round 2 的 I2),修法是"一份常量、多处 splice、
锚点测试对变体循环"。新 prompt 直接生在这个安排里,不必先挣一次自己的
第三次分叉。八类目名缺席断言、判据锚点、独门判据锚点都对变体循环。

**核心里写死的东西**(每条都有测试钉):
1. **不对称性铁规**(§3.2 的 prompt 落地):停在锚点是**默认**;菜单是**换线**
   的证据,从来不是留下的证据;词面重合是换线的**必要不充分**条件。
   数据依据:续接轮 26.2%–71.6% 锚点不在 BM25 top-3,8.2%–49.3% 锚点零分 ——
   "同簇后续轮零词面重叠"是常态而非信号。
2. **continuity 的独门判据折进核心/条件块**:业务意图级
   粒度、**agent 自己的回答引出的追问算续接**(共享核心);遗留桶规则在
   `ANCHOR_NOT_CONTINUABLE_NOTE`(08-27 起,见上一条)。continuity
   作为决策者被替换了,只有它的 prompt 说过的东西必须活下来 —— 尤其第二条,
   真机上正是它缺席导致三问三线碎片化(agent_846942113533 轮 3)。
3. **no_topic 判据一字不改**。2026-08-21 的裁定摆着:调词的边际收益已归零
   (四条硬误判全是判据明文排除的形态),而一句倾斜句换来碎片化 +0.186。
   合并改的是**谁在问**,不是话怎么说。

`participant` 出口**只在** participant 变体里出现:给一个背后没有候选的出口
等于邀请一个越界 index(而越界 index 在实现里等于一次失败兜底)。

## 2026-08-25 — no_topic 判据抽成共享常量 `_NO_DURABLE_TOPIC_RUBRIC`(PR #361 round 2, I2)

两份 judge prompt 的**第三次**静默分叉被 review 抓到:P1 校准(两条压倒
规则+三反例+边界/平局规则)只写进了主变体,participant 变体(IM 群聊、
被邀用户的每一轮)一直用未校准判据裁决——而那个判据的实测误判率是
20.8%(M6),且该路径回放语料照不到。修复:判据抽成一份常量,f-string
插进两份 prompt(两份都无字面花括号,已验证);三个校准锚点测试改为对
两个常量循环断言,分叉第四次发生时测试先红。participant 独有的判定
优先级(participant 高于一切)与 matched_index 取值差异**刻意不抽**。


## 2026-08-21 — 八类目词表从两个 judge prompt 移除(推翻 08-16 的"必须留着")

08-16 的 ⚠ 预警("拆词表 = 压成从未测过的二元判断")当时是对的谨慎,现在被
真机证据推翻:judge 推理原文出现"根据分类规则…归为 GeneralOneShotQuestion"
——词表在教 judge **先分类、再因命中类目而判 no_topic**,7 轮实测 3 轮进该
出口(43%),其中一次误锚引发线身份劫持(todo/2026-08-21-frozen-anchor-
identity-wash-hijack.md)。P1 的 Boundary 段落还**明文豁免**类目 4/5/7,
轮 5(问能力)/轮 6(搜新闻)正是从豁免口进去的。

改动(两个 prompt 同步,主版+participant 版):
1. 八类目列表 + "DESCRIPTIONS, not destinations" 段 → 第一性原理定义:
   **requests nothing and refers to nothing**(纯问候/致谢/告别/情绪/确认,
   在任何对话里读起来都一样)才是 no_topic,没有类型清单可归
2. Boundary 段重写为请求判据:点名任何可命名的请求(哪怕一次性问题、
   哪怕问 Agent 能力)都携带话题;**存疑时 prefer NEW over NO_TOPIC**——
   薄新线可找回可合并,冻结误档的轮次永远找不回(劫持案的教训)
3. P1 的两条压倒规则 + 三条陷阱反例**原样保留**
锚点测试同步翻转:`test_judge_instructions_dropped_the_eight_category_names`
(双 prompt 断言不含)、`test_no_topic_boundary_is_request_based_not_
taxonomy_based`。已知风险(如实):真寒暄误翻(P1 跷跷板)与琐碎问题
建线增多(G3/G4),换来的是倾倒入口收窄+夺舍燃料减少;考卷复验待跑,
本次先真机验证。

**同日第二刀:CONTINUITY_DETECTION_INSTRUCTIONS 的词表也删了**(第一刀
扫尾不彻底,铁律 #8 的教训再+1)。真机 agent_846942113533 轮 3:连续性
输入里明明有上一轮问答("你能做什么"+ agent 的回答),"如何连接im平台"
是对回答的直接追问,判据 4 本该命中,却被词表的"AgentHelpAndCapability…
一旦涉及具体内容就该切走"压过 → 判不续 → 三问三线碎片化。改动:
"8 Special Default Narratives" 整块 + 判据 1/2/3 里三处"the 8 default
Narratives"引用移除;存量迁移语义保留为一段不含类目名的
"[Special Default Narrative] = legacy shape-container"说明(prod 9080
条存量桶的老会话仍会遇到该标签,行为与 C-2 修复一致:按内容判,
明显续接上一轮即 true)。绝对锚点:全 prompts.py 八类目名出现次数 = 0,
由同一条翻转测试对三个 prompt 常量断言。

## 2026-08-16 — 两条 prompt：judge 的词表化，连续性的去容器化（C-1 + C-2）

**`NARRATIVE_UNIFIED_MATCH_INSTRUCTIONS`**：八个类目从"可选中的目标"降为**识别用
的词表**，判据出口改成 `matched_category = "no_durable_topic"`。同时补了一条正向
排除规则：**消息只要点名了具体东西**（文件、项目、工具、报错、人、任务、交付物）
或在延续已经在做的事，就**不是**"无持久话题"——按它指向什么判，不按它多短多随意。
这一条针对的是残差类目（GeneralOneShotQuestion / UnclassifiedOrGarbage /
CasualChatOrEmotion）"定义上永远匹配得上"的结构缺陷。

⚠ 词表**必须留着**。八个名字是 judge 认出"这没有可沉淀话题"的脚手架；连容器带脚
手架一起拆掉，等于把它的分类学从 8 类压成一个**从未测过**的二元判断。这条风险已
预注册为 M6（judge 判"无持久话题"里实际是实质话题的比例 ≤10%），
`test_judge_instructions_keep_the_eight_category_names` 钉住词表不许被顺手删掉。

**`CONTINUITY_DETECTION_INSTRUCTIONS`**：删掉三处"因为当前是 default 所以 must
判不属于"的**无条件容器规则**，改回判"是否延续同一个业务目标"。C-2 实测：34 条连
续性漏接里 **21 条（61.8%）**锚点是桶，其中 5 条判词自己承认话题在延续却仍判
False。与 ⑤ 是同一个闭环的两条边，必须同批上线。
## 2026-08-12 — `NARRATIVE_SINGLE_MATCH_INSTRUCTIONS` 删除（消费者已死）

它的唯一消费者是 `_retrieval_llm.llm_confirm`，而那条链路（`retrieval.
_prepare_candidates` → `_llm_confirm` → `llm_confirm`）是一个零外部入口的闭环，
随本次一并删除，理由见 [[_retrieval_llm.py]] 的 2026-08-12 条目。`prompts_index.py`
的索引项同步删除 —— 索引项指向不存在的常量会直接 ImportError，所以这两处必须同
commit。

顺手校正一处**旧文档的不准确**：《四个待修缺陷》文档把本文件里的
`class NarrativeMatchOutput(BaseModel):` 记成"prompts 文件里又定义了一遍"。实际它
在那段 prompt **字符串内部**（给 LLM 描述输出格式的文本），不是第二个类定义。删掉
整个常量后两者都没了，但结论不能照抄成"删掉了一个重复类"。

## 2026-07-28 — R4d：Created At 迁入 turn 版（created_at 有两个时钟源）

R4c 给时间戳做了唯一规范化渲染（`_canonical_timestamp`），修的是**格式**；
R4d 修的是**取值来源**——这是两个不同问题，前者不能覆盖后者：

- `NarrativeRepository._entity_to_row()` **不写** created_at/updated_at，
  INSERT 走 schema 默认 `(datetime('now'))`，取的是 **DB 时钟**（秒级）。
- `_narrative_impl/crud.py` 里 `now = datetime.now(timezone.utc)` 在两次
  proxy 往返 + save **之前**捕获，构造的内存对象取的是 **Python 时钟**（带微秒）。
- 于是**创建 narrative 的那一轮**渲染出的秒数，可能与之后每一轮从 DB 回读
  渲染出的秒数不同（只要那两次往返跨了秒边界）。规范化后两者都是 23 字节，
  差异是**等长替换**——`[SYSPROMPT-BREAKDOWN]` 的 per-part 字节数看不见，
  但缓存前缀在此处（模板内约 1051 字节偏移，E2 标记的断点区）被打穿。

修法选择（两条路，选了前者）：

1. **（采用）把 `- Created At: {created_at}` 从 STABLE 模板迁到 TURN 模板。**
   稳定半从此**不含任何时间戳**，"哪个时钟"这个问题对缓存不再有意义；
   prompt 表面只动一行；不改数据层，无 DB 行为风险。铁律 #16：迁移不是丢弃，
   模型每轮仍在 turn 块看到创建时间（`- Created: ...`）。
2. （未采用）让 `_entity_to_row` 显式写入 Python 侧 created_at/updated_at。
   它修的是数据层不一致（本身也是真问题），但**缓存前缀里仍留着一个时间戳**——
   任何未来的第二个写入路径、时区/精度差异、乃至 MySQL `DATETIME(6)` 与
   SQLite TEXT 的回读差异都会再打穿一次；而且改 INSERT 语义触碰铁律 #6 的
   谨慎区（entity.created_at 为 None 时会写空）。故记为已知问题而非本次修法。

至此 STABLE 模板的四条易变行全部迁出：`Updated At`（R4a）、`Name`（R4c）、
`Current Summary`（R4a）、`Created At`（R4d）。留下的 description + actors
在 CLI session 生命周期内恒定（actor 变更是结构性成员事件，属合法一次性打穿）。
**改共享文案仍必须同时改 MAIN 与 STABLE 两处**，等价性（稳定版 = 完整版减去且
仅减去这四行）由 `tests/narrative/test_narrative_prompt_split.py` 锁定。

## 2026-07-28 — R4c：Name 迁入 turn 版（E2 实证 Name 是 LLM 每轮可变字段）

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

E2 实验（`reference/self_notebook/specs/2026-07-25-e2-request-capture-findings.md`
§3）逐字节比对证明 narrative Name 在轮间从"query 截断草稿名"漂移到 LLM 定稿名，
是 system[2] 前缀 ~1.2K 处的断点之一。**判定依据**：updater.py:386 每次 LLM
update 都重写 `narrative_info.name`（与 current_summary 同源、同频），它没有
可用的"canonical 稳定形态"——所以选**迁出稳定块**而非规范化渲染。STABLE 模板的
"Narrative Details" 只剩 Description（updater 从不改 description）；TURN 模板
现承载 name + updated_at + current_summary 三个字段。actors 留稳定块（结构性
成员变更 = 合法一次性打穿）。MAIN 模板（开关关的 legacy 路径）不动结构。

## 2026-07-28 — R4a：NARRATIVE_MAIN_PROMPT_TEMPLATE 拆出稳定版 + turn 版

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

新增 `NARRATIVE_STABLE_PROMPT_TEMPLATE`（= MAIN 模板逐字节减去
`- Updated At: {updated_at}` 与 `- Current Summary: {current_summary}` 两行）与
`NARRATIVE_TURN_PROMPT_TEMPLATE`（`## Current narrative state` 块，恰好承载这两
个每轮易变字段）。MAIN 模板**原样保留**——relocation 开关（见
[[context_runtime.py]]）关闭时走它，保证字节级回滚。**改共享文案必须同时改 MAIN
与 STABLE 两处**，等价性由 `tests/narrative/test_narrative_prompt_split.py` 锁定。

# prompts.py — narrative 子系统的全部 prompt 常量

## 2026-08-27(round 3)— 合并段迁往 prompts_merged.py

~220 行合并指令片段+composer 独立成文件;`_NO_DURABLE_TOPIC_RUBRIC`
**留在本文件**(judge 变体也逐字 splice 它,唯一拷贝是全部意义),
prompts_merged 反向 import 它。

## 2026-08-27(round 2)— 共享核心自己也在邀请被拒的 verdict(C1 残留)

上一轮把答案表参数化了,但共享核心块里"Staying on that thread is the
DEFAULT answer"仍是无条件的——无锚点/桶锚点的轮上,user_input 说"不能续",
核心却说"留下是默认"。拆法:核心只留锚点无关的两块(业务意图粒度、
读菜单+"词面重合必要不充分");不对称性段落+上一轮判据块 →
`_MERGED_CORE_WITH_ANCHOR`(仅可续时渲染);不可续时渲染对称的
`_MERGED_CORE_WITHOUT_ANCHOR`(自带省略式读法一行)。变体测试同步:
DEFAULT 句 = 仅可续变体断言有、不可续断言无。

## 为什么存在

narrative 子系统有多个 LLM 消费面：主 system prompt 的 Narrative 段
（PromptBuilder）、continuity 判定（ContinuityDetector）、检索仲裁
（NarrativeRetrieval 的 single/unified match）、元数据增量更新
（NarrativeUpdater）。所有 prompt 文本集中在这一个文件，逻辑代码零内联字符串，
prompt 审计只看这里。

## 上下游关系

**被谁用**：[[prompt_builder.py]]（type/actor 描述常量 + 三个 NARRATIVE_*_
PROMPT_TEMPLATE）；`continuity.py`（CONTINUITY_DETECTION_INSTRUCTIONS）；
`_retrieval_llm.py`（UNIFIED_MATCH 两版：带 / 不带 PARTICIPANT）；
`updater.py`（NARRATIVE_UPDATE_INSTRUCTIONS）。

**依赖谁**：无。零 import，纯常量文件。

## 设计决策

- 8 个特殊 default Narrative 的边界规则写死在 continuity/unified-match prompt
  里——它们是平台级通用分类，不是场景逻辑（铁律 #4）。
- NARRATIVE_UPDATE_INSTRUCTIONS 强制 current_summary 为结构化 fact-sheet
  （bullet、8-12 条上限）而非散文——它每轮被 LLM 重生成，是 system prompt 增长
  的头号来源（见 [SYSPROMPT-BREAKDOWN] 的 nar_summary_chars）。
- 模板占位符与 Narrative 字段一一对应，注释块里逐个列出——加占位符必须同步
  prompt_builder 的 format kwargs，缺一个就 KeyError。

## Gotcha / 边界情况

- 文件里有**两处**关于 "Narrative main system prompt template" 的注释块头
  （历史遗留，第一处在 :40-52 只有注释无常量），真正的模板在文件尾部。
- MAIN/STABLE 模板以 `"\n## Narrative System"` 开头（前导换行），与
  context_runtime 的 `"\n\n".join` 组合产生刻意的段间距——改前导空白会静默改变
  system prompt 字节。

## 2026-08-19 — P1 校准：那条正向排除规则**没有转移**，M6 实测 20.8%

上一节押的注(补一条"点名了具体东西就不是无持久话题"的正向排除规则)
**被 after-run 证伪**：`data/replay_runs/2026-08-19/` 两跑对比里

- **M6 = 20.8%(11/53)**，判据是 ≤10%；最严读法(边界样本全算判对)仍有 15.1%。
- judge 簇首轮判"无"的比例从 **21.4% 飙到 94.7%** —— 桶被拿走后，
  judge 把"残差类目"整个倾倒到了 `no_durable_topic` 上。词表留着了，
  **但残差倾倒换了个出口继续发生**。

机制上这比进桶更隐蔽：④-A′ 下 `no_topic_anchored` 会挂上活跃线但**刻意不触发
updater**，所以被误判的实质话题**内容永远进不了检索面** —— 与"进桶只进不出"
同一类伤害，只是不再有一条 default 行可以指着说"它在这儿"。

**本次改法**(只动措辞，八类目与"新建是最后手段"都没动)：

1. 两条压倒性规则：点名任何具体物件/任务/问题/规则 ⇒ NEW 而非 NO_TOPIC；
   **不许为了少建线而选 NO_TOPIC**(后者直指上面那个倾倒动作)。
2. 三条反例，来自普查实际丢轮的三种形状：礼貌开启句包着请求、祈使短句、
   为将来设的规则。抽象规则上一轮已被证明**自己传不下去**，所以这轮写成反例。
3. ⚠ **边界句不是可选项**。"点名任何具体……问题……就是 NEW"字面上与保留的
   类目 4/5/7 直接冲突(问 Agent 自己、一次性人格指令、闲聊问题都是"问题/规则")。
   花钱前的反向核对预测 `怎么变帅`、`你在干嘛` 会被误翻，因此加了以
   **"是否指向用户自己的工作/世界"**为准的边界句 —— 靠指向判，不靠列例外。

三条都由单测钉住(`test_judge_instructions_carry_the_p1_no_topic_narrowing` /
`..._three_trap_counterexamples` / `test_p1_narrowing_does_not_swallow_shapes_4_5_and_7`)。

⚠ **这是预注册的一次性校准**：预测写在
`data/replay_runs/2026-08-19/P1_CALIBRATION_PREREGISTRATION.md`，**复验不过就回
设计层，不许再调词** —— 对着同一份考卷反复改措辞就是过拟合，M6 会失去意义。

~~⚠ **未动但已知不一致**：participant 版仍把八类目当可选目的地~~
→ **已修,见下一节(2026-08-20)**。

## 2026-08-20 — participant 版补上同一刀(P0 范围内的执行遗漏)

P0 把八类目从"可选中的目标"降为词表时**只改了不带 PARTICIPANT 的那一版**。
`_retrieval_llm.py:89-91` 二选一,于是 IM 多人场景(有 participant 候选时)
仍在对模型推销 `matched_category = "default"` + 一个索引,而桶已经不再播种、
也不再进池 ⇒ `default_candidates` 必为空 ⇒ 模型选它必然越界,只留一条
`out of range` 警告然后静默落到"无匹配"。**等于 participant 场景下"寒暄识别"
被悄悄关掉。**

本次按主版的同一套删除逻辑处理:八个名字**留作识别词表**(连同那句
"These are DESCRIPTIONS, not destinations"),出口从 `default` 改成
`no_durable_topic`,participant 优先级与其余措辞一字未动。

⚠ **刻意的分叉**:主版带着 P1 的收窄措辞(两条压倒性规则 + 三条反例 + 边界句),
participant 版**没有**。因为 P1 复验 M6 严读法 13.3% 未过、已停在设计层,
把一个没通过判据的措辞复制到第二处只会放大它。两版现在**结构一致**
(都不提供桶目的地、都有 no_durable_topic 出口、都保留八类目词表),
**收窄程度不一致** —— 等设计轮定了出口形状再统一。

### 铁律 #8 扫尾:八类目名全仓 grep,逐处判定(无第三处漏网)

| 位置 | 判定 |
|---|---|
| `_narrative_impl/default_narratives.py` 的 8 条种子定义 | **留**。播种由开关跳过,但定义在开关=1(回滚)时仍需要,且存量行的名字指向这里 |
| `CONTINUITY_DETECTION_INSTRUCTIONS` 的"8 Special Default Narratives"段 | **留 —— 承重,不是死文本**。`narrative_service.py:322-332` 的跳过分支 gate 在 `not NARRATIVE_DEFAULT_BUCKETS_ENABLED`:开关关时该段不可达,**开关开(一行回滚)时它是活的**。删了会打断回滚路径 |
| `backend/routes/me.py` 的 `include_default` 参数说明 | **留**。读侧过滤器,存量桶行永不删除(铁律 #6),这个说明仍然准确 |
| `narrative/config.py` 的注释 | **留**。注释 |

⇒ 真正的漏网只有 participant prompt 一处,本 commit 已修。

### 覆盖面的诚实口径(必须留痕)

**这次改动只有单测护体。** 重演考卷(`data/replay_runs/` 全部 18 序列)
**PARTICIPANT 零触发**(三臂的 `selection_method` 分布里没有 `participant`),
所以本次改动的行为效果**在评测上完全没有覆盖**,M6/G 全套指标都测不到它。
prod 的 IM 多人场景是**盲区**。发版后应按 §D.3 的只读口径专门看一眼
participant 路径的 `judge_category` 分布。