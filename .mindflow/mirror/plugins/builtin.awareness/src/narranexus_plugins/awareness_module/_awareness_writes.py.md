---
code_file: plugins/builtin.awareness/src/narranexus_plugins/awareness_module/_awareness_writes.py
last_verified: 2026-08-20
stub: false
---

## 2026-08-20 (十六改) — level 钉死回归;十五改那句「两处 except 复活」是错的

**C1(回归)**:十五改的 `_scan` 只在「同级或更高」那一支更新 `level`,于是文档**首个
标题**把它钉死。`# Agent Awareness Profile` 之后,`## 4` 经 elif 开启身份节但
level 停在 1,`## 5` 要 `2 <= 1` 才能关——永假。**身份节之后的 owner 小节全部
可编辑**;修复路径还会从泄漏的 `## 5` 里把 owner 的名字(裸名,无分隔符,过全部
inferred 检查)读成 agent 的自述。dev 上 H1 被忽略、同一文档是安全的——严格回归。

修法:elif 开启身份节时**若此前不可编辑**则 `level = depth`;已可编辑不更新(否则
`### Role Definition` 抬高 level 后,its sibling `### Background` 会关掉身份节)。
`TestScopeAfterAShallowerFirstHeading` 两格钉住,含 `declared_self_name` 越界取值。

**十五改的更正**:那节末尾写「两处 except 复活」——**错了,只活了一处**(reconcile)。
改名路径的旧名来自行、不推断,`retire_self_name` 对它永不 raise,所以
`record_identity_change` 那处 except 是结构上永远不可达的,已删,原地留注释说明
为什么不要加回来。这是「doc claim 超前于 code」第六次,方向教训同前:**凡是写
「已修/已活」,先跑一遍再写**。

**十五改的代价,记下而不是让人推导**:裸名声明含空白或 `- — / | .` 者(含十改损坏
产出的 `小绿-2-2-2`,以及 `John Smith` 类多词拉丁名)**永不自动修复**——每次对账
只留行、写矛盾记录、报 `False`。要自动修复这批需要一个不靠猜的输入(比如行上落一个
last-known name),单独的活。测试
`test_a_name_containing_a_separator_is_never_auto_repaired_only_reported` 钉住。

**已知残留(定界,非漏判)**:不含空白与弱字符的连续 CJK 值(`- 名称：美食家的助手`)
与裸名**不可区分**,仍按裸名处理、会被替换成行上的名字。`美食家的助手`完全可能就是
名字,任何启发式分不出这格——这是判据的下界。

## 2026-08-20 (十五改) — 推断来的旧名不再可信;十二改的 raise 复活

**C1(数据丢失)**:没有强开启符时 `_name_part` 返回**整行值**,而修复路径把它当
「已知旧名」喂回 `retire_self_name`——于是 `value.startswith(old)` 恒真、
`value[len(old):] == ""` 恒满足声明判据,**名字之后的全部文本被替换成当前名字**:

```
'- 名称：美食家 是 owner 最近常去的那家店'  →  '- 名称：小绿'
'- 名称：小绿 - 美食推荐专家'              →  '- 名称：小绿'   ← 根本没改名
```

第二、三种形态只需要一次**同名保存**(`PUT` 传当前名 → `unchanged` 分支 → 对账)。
`instance_awareness` 是覆盖写,那些字在任何地方都不再存在;接口同时返回
`identity_record_updated: true`,前端只在 `=== false` 时弹窗,所以用户收不到提示。

这是同一处推断的**第四种**损坏,也是唯一一种删掉 agent 自己写的字、而不是把名字弄
错的。十三改把强/弱分隔符分开,只修了「推断出错的名字」,没修「**推断出的边界被当成
可信旧名喂回改写器**」这个结构。

修法:`retire_self_name` 增 `inferred: bool`。推断来的旧名只在两种情况下可用——
边界真的被找到(`_boundary_was_found`:行值比推断名更长,即被开启符截过),或声明确
是裸名且不含空白/弱字符(`_declares_bare_name` + `_has_weak_separator`)。否则
**raise**。

**C2**:十四改拆掉前缀护栏时把唯一的 `raise _AmbiguousSelfName` 一起带走了,却留下
类、两处 `except`、`refused` 变量(铁律 #2 的死代码),而本文件十二改那一节还在把它
写成现行机制——读者会以为 `identity_record_updated` 已经覆盖「行没退休成功」,而那
正是 C1 静默的直接原因。现在触发条件换成「推断边界不可信」(不是十二改被否掉的
「前缀重叠」——`小绿 → 小绿2` 是精确改名,不能被当歧义拒绝),两处 except 复活。

⚠ **十二改那一节的机制保留,触发条件已被十三改/十五改替换**——读那一节时以本条为准。

其余:`_ANY_H2` → `_ANY_HEADING`(任意层级参与,但**只有同级或更高级**才结束一节,
否则 `### Role Definition` 会关掉它所属的身份节);`_declares(value, name)` 谓词抽
成一处(此前同一判断在读侧和写侧写法相反)。

## 2026-08-20 (十三改) — 分隔符分强弱:只有描述开启符能结束一个名字

第十三轮 C1:agent 叫 `小绿-2`,自述行 `- 名称：美食家-资深；精通各地美食推荐`,
名字被推断成 `美食家`(第一个分隔符之前,而那个分隔符是**连字符**),改写后变成
`- 名称：小绿-2-资深；…` ——**一个从来不存在的名字**,而系统随后认为它是对的。

**同一个推断在三轮里造出三种损坏**:

| 轮次 | 形态 |
|---|---|
| 十改 | `小绿-2` 读成 `小绿`,每次调用再加一次前缀 → `小绿-2-2-2` |
| 十二改 | 前缀重叠时拒绝改写,但**静默**,接口报"记忆已正确" |
| 十三改 | 拿推断出的旧名去改写 → 造出 `小绿-2-资深` |

三者都需要同一个前提:**连字符 / 斜杠 / 点号算作名字的结尾**。而它们恰恰活在名字
**内部**(`小绿-2`、`netmind/ops`、`v1.2`)。

修法(Owner 2026-08-20 定:允许猜,但只允许一种分隔符):`_NAME_ENDERS` 只保留
**描述开启符** `；;，,、。(（`。名字**穿过**弱字符一直走到第一个开启符。于是:

- `小绿-2` → 名字就是 `小绿-2`,与行相符,**不动**
- `美食家；精通各地美食推荐` → 名字 `美食家` → 改写成 `小绿-2；精通各地美食推荐`,描述保留
- `美食家-资深；精通各地美食推荐` → 名字 `美食家-资深` → 同样改成 `小绿-2；…`,**不再造名字**

名字本身含开启符(`美食家；顾问`)的情形由 `value.startswith(current)` 那一支兜住,
不走推断。

我在这条上先砍太宽过一次(把整套边界推断连测试一起删掉),结果对**线上真实形态**
`名称：X；描述` 完全不生效——而实测正是那一行让模型答了旧名。已回滚,按精确根因重做。

## 2026-08-20 — 门禁的判据从「看文件」改成「看那次调用」

合并 dev 最新 7 个提交后,`test_only_one_writer_of_agent_name` **当天就红了**,抓到
新增的 `backend/onboarding/provisioning.py`。门禁起了作用——但那是**假阳性**:那个
文件通过 `provision_new_agent`(白名单里的创建路径)建 agent,自己的 `update_agent`
只写 `agent_metadata`;`agent_name` 只是它生成后**交给创建函数**的一个值。

第二条判据当初为了抓「已在白名单里的文件再加一次同类写入」,用的是
「文件写了这一行 **且** 文件里出现过 `"agent_name"`」——粗到会把合法的创建路径算成
违规。而一个在正常改动上误报的门禁,第二次就没人看了(第十二轮刚因为同一个道理
批过 `identity_record_updated` 恒真)。

改成 **AST 判定**:遍历该文件里的写入调用,看**那次调用本身**有没有把值放进
`agent_name`(字面量 key,或 `agent_name=` 关键字,含包在
`normalize_agent_row_text(...)` 里的)。粗判据(文件+写法的等值白名单)保留作第二道网,
因为 AST 看不见「dict 存在变量里再传进去」这种形状——事务自己就是那个形状。

**改完重新验证过它还会拦**:往白名单文件里偷加一次
`update_agent(aid, {"agent_name": ...})` → 红;恢复 → 绿。判据变细了,拦得住的形状
没变窄。

## 2026-08-19 (十二改) — 「拒绝退休」必须能被观测

十改给 `retire_self_name` 加了歧义保护(旧名与新名互为前缀时拒绝改写,理由正当:
无法从行内判断名字边界)。但**拒绝这件事没有任何返回通道**,两个写入器照样报成功。

触发它的是**最常见的改名形状**:给名字加后缀。`小绿` → `小绿2`、`Ann` → `Anna`
都成立。结果 profile 里 `- 名称：小绿` 在前、`You are 「小绿2」` 在后——正是本文件
2026-08-19 实测记下的那个形态(模型跟了前一句)——而接口返回
`identity_record_updated: true`,UI 一句都不弹。**这个字段在它最该报警的路径上恒为真,
比没有这个字段更糟。**

改成 `raise _AmbiguousSelfName(profile)`,而不是返回原样的 profile:返回值一样的
东西,调用方分不出"没动"和"退休成功",而分不出正是它一直隐形的原因。记录照常写入
(它才是告诉 agent 当前名字的东西),但写入器返回 `False`。

既有测试全部用 `美食家`/`小绿`——**无前缀关系,正好绕过这一支**。已补前缀重叠那格。

## 2026-08-19 (十一改) — 对账两个来源**都**查，且扫描器这次真的共用了

九改让对账认「记录与行不一致」，漏了只有陈旧自述行的那批；那次的修法是把自述行
检查放进「没有记录段」分支里——**做成了二选一**。于是十一改抓到镜像的那一半：
**记录已经改对、自述行还留着旧名**的 agent 永远修不到，接口报纯成功。

同一个函数、隔一轮、同一个「两条路只走一条」。现在两个来源每次都查：
`_asserted_name(profile)`（记录最新一条断言的名字）与
`declared_self_name(profile, current_name)`（自述行），任一与行不符就修，都不符
才返回 `None`。

**扫描器这次是真的共用了**：十改声称 `retire_self_name` 与
`_identity_section_lines` 共用「文档哪一段属于 agent」的判定，实际那边还留着一份
手抄的遍历——只改了 docstring。现在两者都走 `_scan()`，它 yield
`(line, is_the_agents_own_section)`。

顺带一处收敛 bug：`retire_self_name` 把名字**原样**写进单行声明，而名字可以含换行
（`_for_note` 那条恶意用例），文档被劈成两行、下次只读到前半段 → 每次都"修" →
不收敛。改成写入时走与记录相同的 `_for_note` 转义，比较时也用同一形式。

## 2026-08-19 (八改) — 退休范围改成正向匹配身份节

上一轮用「排除编号 1/2/3」来限定，第八轮指出这留下了**模型自己新增的每一节**——
包括本次改动自己的 fixture 里那个 `## 5. Owner observations`——都在可编辑区内。
更糟的是本 PR 两份 fixture 对 `## 5` 给出了**相反**的含义（一处当身份节、一处当
owner 观察），而代码实现的是危险的那一份。

改成**正向匹配**（标题含 role / identity / 身份 / 角色 / 自我认知，或首个 `##`
之前的前言）。判据反过来的理由是**两种失效代价不对称**：

- 漏掉身份节（模型改了标题）→ 旧自述行留着：**可见、可恢复**，而且它下面那条身份
  记录仍在纠正 agent；
- 误改 owner 的行 → `instance_awareness` 覆盖写，原值**在任何地方都不再存在**。

所以默认是"不动"，并且**被跳过的候选行也打 logger.info** —— 这样"标题漂了、退休
静默停止工作"是可查的，而不是不可见的。测试补了 owner 名字行落在**非编号**小节
里的那一格（原来那批测试全绿也测不到它）。

## 2026-08-19 (五改·验证) — 提示词已全对；剩下的是对话历史，不修

自述名字行修好之后又跑了一次真机两轮。**turn 2 实际组装出的系统提示**（在
`ContextRuntime.build_complete_system_prompt` 上挂钩抓的原文）里，
`**Agent Name**`、`- 名称：`、身份记录三处**全是新名**，「美食家」只出现在那条
退役它的更正句里。

但 turn 2 的回答仍是旧名，模型写的理由是「The agent's context shows: Agent name:
美食家」——**系统提示里没有这句话**。它来自 `--resume` 复原的上一轮对话记录：
turn 1 时它确实叫美食家并如此自我介绍过。

对照：**全新对话**里改名后问「你是谁」→ 答新名（`events` 可查）。

所以边界画在这里：**平台保证自己说的每一句都是真的**（已做到并逐字节验证），
agent 自己说过的话不改写——那是用户的对话数据，而模型更信自己上一句还是更信
系统提示，是铁律 #15 明说不干预的 LLM 侧特性。复测要在新对话里做，详见
`reference/self_notebook/todo/2026-08-19-rename-vs-ongoing-conversation.md`。

## 2026-08-19 (五改) — 平台开始改写 agent 自述的名字行

**实测把上一版推翻了**。改完四轮之后，平台拥有的每一处状态都对了：`agents` 行、
BasicInfo 的 `Agent Name`、身份记录、同伴名录。然后跑了一次真实两轮对话——
turn 1 它叫美食家并如此自我介绍，改名回小绿，turn 2 再问：**它还是答美食家**。

原因在 profile 自己身上：

```
## 4. Role and Identity
- 名称：美食家              ← agent 自己写的，从没人动过
## Identity Changes (platform record)
- ... You are 「小绿」...    ← 我们写的更正，在它下面
```

提示词里同时有两句话，前一句在前面，模型跟了前一句。

第三轮审查建议**别动** section 4（理由：由 agent 的 `update_awareness` 重写、
且 08-04 立过「不该编辑」原则）。照做的结果就是上面这个。Owner 2026-08-19 定：
**改名时一并修正自述名字行**。

界线划在哪：08-04 那条原则保护的是 **agent 对 owner 的观察**——为改名丢掉它会比
原 bug 更糟。而**名字**不是观察，它是 `agents` 行拥有的、机器可知的事实，铁律 #15
明说机器可知的事实要推导，不能指望模型记得。

`retire_self_name` 只改**名字声明行的值**（`- 名称：X` / `Name: X` 等，且值以旧名
开头），其余一律原样。测试里专门有一条 owner 观察句里含旧名（「owner 说他上次在
美食家那家店吃过饭」），必须不被改写。

⚠ **接线漏了一次**：`retire_self_name` 与 `_note_is_readable` 的第一版补丁字符串
没匹配上（那段代码用的是 `awareness_repo` 不是 `repo`），于是函数定义了、单测绿了、
**从没被调用**。是端到端那条断言抓出来的。函数有单测 ≠ 它被接进了流程。

## 2026-08-19 (五改附) — 文件头重写

本文件头还在描述已经搬去 [[_overview]] 的事务。已改成描述真正留下的东西：身份记录
那一段的常量与助手、两个写入器、`retire_self_name`、以及 MCP 渲染器。

## 2026-08-18 (四改) — 事务搬去 [[_overview]]，本文件只留身份记录那一步

第三轮审查：改名事务住在**可热插拔的 Module** 里，而两个核心平台路由 import 了它。
把 AwarenessModule 从 `module_registry` 摘掉不是功能降级，是 route import 期
ImportError——**后端起不来**（铁律 #3 的方向被反过来了）。而本文件上一版那句
「别再新增写入方，要写就调这个函数」，等于用文档把这个反向依赖固化。

事务（`AgentProfileWrite` / `apply_agent_profile_change` / `_stored_text_is_unnormalized`
/ `_same_owner_name_holder` / `_refresh_peer_directory`）搬到
`narranexus/platform/agent_profile/`。本文件留下的是**真正属于 Awareness 的那一步**：
`## Identity Changes` 段的常量、三个字符串助手（build ×2 / `identity_note_asserts`）、
`merge_identity_change_note`，以及两个 DB 写入器——
`_record_identity_change` / `_reconcile_identity_record` 改成**公开**
（去掉 `_` 前缀）并从门面导出，因为现在由包外调用；私有实现不跨边界外露。

`update_agent_profile_from_args`（MCP 渲染器）留在这里并**调用**新包：Module 依赖
核心领域包，方向对；核心不依赖 Module。新包那边是**延迟 + 容错**导入本模块，
所以 Awareness 缺席时改名只降级一步，不是整体失败。

顺带：本文件从 679 行降到 328 行（拆分前已逼近 800 行上限）。

**护栏表第 8 行随之更新**：`repo.update_agent` 那一行的文件已从本文件改成
`agent_profile/_agent_profile_impl/profile_write.py`。命令本身不变。

（四改补记：三改写下这句时**表本体没改**，第四轮审查按命令实跑对出来的。同一轮里
我已经因为「断言写在验证之前」被抓过两次，这是第三次。凡是声称"某某已更新"，
**先跑一遍再写这句话**。）

## 2026-08-18 (三改) — 等值短路问错了问题：未归一化的行修不回来

把 manyfold 的 upsert 折进事务，顺带接管了一个**没人提起过的义务**，是全量测试
红了才发现的（`test_an_omitted_name_rewrites_the_existing_value_normalized`）：
那个包装层原来是**唯一**会把早期/导入产生的未归一化行 `" old "` 重写成 `"old"`
的地方。

为什么非修不可：`agent_field_matches` 比较的是**归一化后**的值，所以 `" old "`
与它将被重写成的 `"old"` 读作"已相等"→ 不发写 → 行保持原样 → 以后每一次比较都
给同样的答案。**那行永远改不了名**。[[entity_schema]] 的 docstring 早就写着
「谓词只在行内文本已归一化时才是可靠的」，并把维持这条当作每个写入方的义务——
我把承担该义务的那个写入方拆了。

修法不是在调用点补一个特例，而是让短路问对问题：

- `agent_field_matches` 答的是**「这两个值是不是同一个值」**——调用方判断"我要的
  状态成立没"要的就是这个，不动。
- 新增 `_stored_text_is_unnormalized`，答**「写下去会不会改变行里的字节」**——只有
  写入方需要。两者只在存储值未归一化时分歧，那正是修复场景。

**陷阱（已钉住）**：修复性写入**不得**记成改名。`" 小绿 "` → `"小绿"` 归一化后
相等，不是改名；若照旧用"进了 updates 就算改名"的判据，身份记录里会多出一条
「从「小绿」改名为「小绿」」。那个 section 的全部价值就是 agent 会相信它，往里
灌噪音比不修更糟。所以 `is_rename`（归一化后不等）与"要不要写"分成两个判断。
测试：`test_normalizing_a_stale_row_is_not_a_rename`。

义务从一个调用方搬到事务里，等于所有写入方都获得了这条修复——原来只有 manyfold
的 POST 走 fallback 时才修。

## 2026-08-18 (二改) — 第四个写入方，以及两处审查修正

同日第一版声称「三个写入方收敛成一个」，独立审查发现是**四个**：
`POST /manyfold/agents` 的 upsert 分支同样覆盖 `agent_name`（见 [[agents]]
二改条目）。断言写在验证之前，正是本仓栽过的同一类毛病。

**护栏现在是门禁，不是请求**：
`tests/schema/test_only_one_writer_of_agent_name.py`。允许清单在**那个文件里**，
因为可执行的那份才是真的——这份 md 只指过去，不再自带一份会漂的拷贝。

它有两层：一层扫「谁在写 `agents` 行」并断言集合**等值**（不是数量，换一处进
换一处出会静默通过）；一层判别性更强——**不在白名单里的文件，只要含写入调用就不许
出现 `"agent_name"` 字面量**。第二层是必需的：第一层的键是 (文件, 写法)，已在
白名单里的文件再加一次同类写入分辨不出来（实测偷加一行 `update_agent(aid,
{"agent_name": …})` 到 `bootstrap/profiles.py`，第一层照样绿）。

写门禁和**验证门禁会拦**是两件事。

改名**没有**不经过 `apply_agent_profile_change` 的路径了。

两处审查修正：

1. **`updated_fields` 不再身兼二职**。原来错误分支往它塞「没落地的字段」，与它
   自己的注释（"were written"）相反：同一个名字在 `ok` 两侧含义颠倒。今天两个
   调用方都先判 `ok` 所以不出错——但下一个做写入埋点的人会直接读它、把失败计成
   写入，而这种错**永不抛异常**，只让指标悄悄偏高。拆出
   `unapplied_fields`。`auth.py` 的错误文案跟着改到新字段（那条分支当时零覆盖，
   改漏了会变成空串 `"The update did not persist: "`，已补测钉住）。
2. **`created_by` 进了 [[entity_schema]] 的谓词**，因为它现在会作为
   `extra_updates` 流经等值短路，而该谓词对未登记字段**故意抛 ValueError**。
   比较方式选的是**逐字节相等，不做 `normalize_agent_text`**：那个助手是给人读的
   展示文本 strip + 截长的，把一个当查找键用的标识符悄悄改形，正是「行被改成一个
   谁也解析不出来的 owner」的成因。

## 2026-08-18 — 改名事务下沉成 `apply_agent_profile_change`，三个写入方共用

深圳线下第二轮 P1（prod `agent_4a0ae5f40af2`，8/14 复测）。取证：`agents.agent_name`
= 「小绿」、`bus_agent_registry.description` = `'小绿: 精通各地美食推荐'`（都对），
而 Awareness profile 里躺着 8/14 15:48 那条身份更正：

> `- 2026-08-14: renamed ... to 「美食家」. You are 「美食家」. 「小绿」 is no longer your name`

方向是反的。agent 先被自己的工具从 小绿 改成 美食家（写下这条笔记），随后被
**界面**改回 小绿——而 `PUT /agents/{id}` 只写列 + 刷名录，**不追加笔记**。于是
profile 每轮原文注入系统提示，用平台的口吻告诉 agent「小绿 不是你的名字」。问
「你是谁」答「美食家」，两次，且它有明确依据。

**这比缺笔记更糟**：笔记是平台声明，agent 会信。所以本次不是"再给 PUT 补一处调用"
——补调用的做法正是 2026-08-04 已经用过的，它撑了 10 天就被第二个写入方绕过。
改成：**改名事务不再由调用方拼装**。

- 新增 `apply_agent_profile_change(db, agent_id, *, new_name, new_description,
  extra_updates)` → `AgentProfileWrite`（frozen dataclass）。名/描述/额外字段的
  归一 + 等值短路 + 单次行写 + 回读核对 + **身份更正** + **刷名录**，全在里面，
  调用方无法只做其中一步。
- `update_agent_profile_from_args` 降级成**渲染器**：把结构化结果格式化成工具历来
  那串字节。DirectStore / [[profile]] 孪生路由的 byte-parity 不受影响（
  `test_agent_profile_tool.py` + `test_profile_seam_route.py` 23 例原样绿）。
- 返回**结构化**而非字符串，是因为另两个调用方是 HTTP 路由，欠客户端一个状态码。
  靠匹配英文散文推状态码，第一次改措辞就断。`error_kind`
  （`nothing_to_update` / `not_found` / `empty_name` / `too_long` / `not_applied`）
  是给机器的，`error` 是给模型读的那一句，两者不混用。
- `extra_updates` 收 `is_public`：它没有身份语义，但放进同一次调用才能保持**单次
  行写**，否则会出现行被改了一半的窗口。它**同样走等值短路**——第一版漏了，
  `test_re_toggling_to_the_current_visibility_writes_nothing_and_succeeds` 当场
  抓到。这条短路不是优化，是本函数方言无关性的地基（no-op 写在 MySQL 返 0、
  SQLite 返 1，下游全部建立在"永远不必解释这个数字"之上）。
- **「无变化」那一支现在也刷名录**（原来提前 return）。理由沿用 [[auth]] #320：
  sync 自己吞失败只返 False，原值重存是用户最自然的重试方式，它不该恰好是唯一
  跳过修复的路。现在三条路径在这点上同语义。

⚠ 给后来者：**别再新增 `agents.agent_name` 的写入方**。要写，调这个函数。理由不是
洁癖——"三个写入方各自记得三件事"就是本条目和 2026-08-04 那条的共同成因。

## 2026-08-17 — 比较上提共享,判据也从 rowcount 换成回读

两处改动,都是和 [[auth.py]] 那半边对齐:

1. 值相等短路改调 [[entity_schema]] 的 `agent_field_matches` /
   `normalize_agent_text`(行为不变 —— 本文件本来就是 strip 后比较,
   只是不再自带一份)。原因见那份 md:同一个输入,本文件判「没变化」而
   HTTP route 判「要写」,两个写入方对 `agents` 行的等价规则不一致。
2. `affected = await repo.update_agent(...)` / `if affected <= 0` 这对**去掉了**,
   改成回读 + 同一个谓词核对。短路已经挡掉 no-op 那一支,但「**真有改动而
   驱动报 0**」这一支原来仍然会答 "Error: the update did not apply" ——
   `cursor.rowcount` 在 MySQL 上数 CHANGED 行,而 agent 读到这句会去重发一次
   其实已经成功的改名。这正是 HTTP 侧 2026-08-17 那次修掉的推断。

**返回串一个字没动**:DirectStore 与 HTTP twin 靠这些串保持 byte-identical
(`backend/routes/agents/profile.py` 直接把它当响应体)。改的是**什么条件下**
返回那句,不是那句本身。

> 措辞更正:本文件 2026-08-05 那次只拆了 **no-op 短路**这一支,module docstring
> 里「陷阱已 defused」的说法当时仅对 agent 侧成立,且 HTTP twin 直到 2026-08-17
> 都还带着原样的 rowcount 判据。现在两条路径在这个 trap 上才真的同语义。

补记(同日 review 第二轮):回读判失败那条分支现在**记一行 WARNING**(字段名 +
「并发覆盖或没落库」),与 [[auth.py]] 同分支对齐。原来一个日志都没有 ——
真发生时唯一的记录是递给模型的那句话,若是并发写造成的,日志与 DB 里没有任何
东西能和它对上(踩着 CLAUDE.md 事故教训 #5 的反面)。**返回串一个字没动**。

再补(第三轮):import 从 `narranexus.platform.schema.entity_schema` 深引改为
`narranexus.platform.schema` 门面。本文件在 `schema/` 之外,用门面没有环。

> 更正(第四轮):上一句原来还写着「manyfold 路由**必须**保留深引,因为成环」——
> **假的**。成环只对 [[api_schema]] 成立(它在 `schema/` 包内,门面反过来导出它的
> 模型);manyfold 在 `backend/` 下,引门面不可能成环(门面不 import backend,
> 这正是本仓的依赖方向)。它当时深引的真实原因只是 `StrippedText` 还没进门面。
> 现在 `StrippedText` 已导出,manyfold 四个符号全走门面。这是同一类毛病的第三次:
> **给一个观察到的现状编一个比事实更强的理由**。

# _awareness_writes.py — update_agent_profile 的共享实现（AgentDataStore seam 单点）

## 为什么存在（PR-9）

`update_agent_profile_from_args` 是 update_agent_profile 工具那套**改名事务**的唯一实现：
写 name/description、往 Awareness profile 追加一条身份更正、同 owner 重名如实告知、写完立刻刷
同伴名录——四件缺一件就退回 prod evt_1f9c6680 的 bug（详见 [[awareness_module]] 2026-08-04）。
从 awareness_module.py hoist 到这里，让 seam 的 DirectStore（本地）与 backend 孪生路由
[[profile]]（云）都调**同一个函数** → 两条路径 byte-identical，改名事务逻辑不在 in-process/Http
之间分叉。身份笔记字符串助手（build/merge + 常量）与两个 DB 助手（_same_owner_name_holder /
_record_identity_change）也一并搬来——它们只被这个 writer 用；放一起还打破 import 环
（awareness_module.py 把工具委托给 seam，故不能反向 import 本文件）。

## 契约

- 返回 **str**（工具历来的状态串，动态：改了哪些字段 + 重名 note），从不因已处理分支抛异常。
- 方言安全（铁律 #6）：AgentRepository / InstanceRepository / InstanceAwarenessRepository +
  db.get，无裸 SQL。**MATCHED-vs-CHANGED rowcount 陷阱**（update_agent 返回 cursor.rowcount=
  SQLite matched / MySQL changed）在写之前被 name/description 两处**等值短路**化解：重复存同值
  返回 "No changes needed"，两方言一致，而不是云上假报 "did not apply"。**别动这两个短路**。
- **长度上限**：name/description 都绑 `AGENT_TEXT_MAX_LENGTH`(255，读模型 `Agent` Field + MySQL VARCHAR(255) 同源)。>255 写会让 agent 行读不出来(get_agent→pydantic ValidationError，NetMindAI-Open#71)且方言分叉(sqlite TEXT 收/MySQL 1406)。检查放**共享 fn**里 → Direct/Http 逐字节同拒(store parity invariant)；路由 body 故意不加 `Field(max_length)`(见 [[profile]]：会 422 抢在 fn 前、破 byte-parity)。
- 未迁：update_awareness 另在 seam（早前 PR）；本文件只管 profile。
