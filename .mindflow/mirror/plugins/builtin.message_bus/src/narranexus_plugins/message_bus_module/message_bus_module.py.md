---
code_file: plugins/builtin.message_bus/src/narranexus_plugins/message_bus_module/message_bus_module.py
last_verified: 2026-09-13
stub: false
---

## 2026-09-13（PR#401）— 编码器 import 路径随迁移改为 `narranexus.platform.utils.inline_field`

仅路径变化，行为不变（编码器迁到 utils 的原因见 [[../../../../../src/narranexus/platform/utils/inline_field]]）。

## 2026-09-11 — 编码器迁到平台层共享（PR#401 同类扫描）

`_inline_field` 及其常量（`INLINE_FIELD_MAX_CHARS` / `INLINE_DESCRIPTION_MAX_CHARS` /
`INLINE_FIELD_CUT_MARK` / `_CODE_SPAN_DELIMITERS`）移到
[[../../../../../src/narranexus/platform/utils/inline_field]]，本模块改为
`from narranexus.platform.utils.inline_field import INLINE_DESCRIPTION_MAX_CHARS, inline_field`。
行为不变；原因是平台侧 `message_bus_trigger` 的团队房 roster / 工作板也要用同一个编码器，而平台层
不能 import 插件。下文历史条目里的 `_inline_field` 即现在的 `inline_field`。

## 2026-09-11 — 行内字段按构造不可伪造（PR#401 review 三轮 🟡1/🟢2-5）

- **根因收口，而不是逐个字符类补洞**：`_inline_field` 是所有行内字段的唯一编码器，分两种形态。**标签**（团队房名、Known Agents 的名字/描述）：折叠空白 → 反引号（含全角 `\uff40`）换成 `'` → 超长按 `INLINE_FIELD_MAX_CHARS=120`（描述 `INLINE_DESCRIPTION_MAX_CHARS=80`）截断且以 `…` 标记 → 以 JSON 字符串字面量输出（`json.dumps(..., ensure_ascii=False)`）。作者写的任何 `` ` ``、` · `、`[`、`]`、` — `、`: `、`(teammate)`、`"`、`\` 都在一对引号里且引号/反斜杠被转义，字段无法提前结束，字面量能原样解码回展示文本。**句柄**（`max_chars=None`：agent/team id、tag 的发送者）：系统生成、不可由作者写，永不截断、不加引号（agent 要原样抄进工具调用），只折叠空白并替换反引号，不能逃出 code span。
- 渲染形状因此变为 ``["Ops" · from agent_x]``、``- `team_x` — "Ops"``、``- `agent_a` — "Alice": "desc" (teammate)``；静态块的示例由同一个 `_bus_tag` 生成，自动一致。仓内没有把这些行解析回来的消费方。
- **`_not_shown_line` 提为模块级纯函数** `(total, kept, rows)`。调用列表只来自被预算让出的行，按新到旧最多列 `NOT_SHOWN_MAX_CALLS=3` 个、其余计数（`and N more conversation(s)`），让出一条行仍可能让声明行变长（它的调用加入列表），所以回让循环不是单调的；上限只是约束了这种增长，循环从最多往下取第一个装得下的条数、且永不少于最新一条；窗口外的消息明说「beyond this list — use read_history on the conversation you expect them in」（没有被让出的行时说「all N are older than this list」，不再用没有先行词的「those」），不再把窗口内的调用摆在全量条数后面；让出的行都没句柄时如实说没有。

## 2026-09-11 — 行内字段同样不可伪造 + 省略口径统一（PR#401 review 二轮 🟡1/🟢2-5）

- **`_inline_field` 收口所有作者可写的行内字段**：团队房名（未读 tag 与 `### Your teams`）、tag 里的发送者、Known Agents 的 `agent_name`/`agent_id`/描述（描述仍截 80），一律把全部空白串（含 `\r`/`\u2028`）折成一个空格并按 `INLINE_FIELD_MAX_CHARS = 120` 截断。房名带换行不再能在列表里另起一行冒充别人的 row。清洗在 `_bus_tag` 内，对静态块生成的示例幂等，静态块不因此变化。
- **「未展示」只有一个数**：声明行写 `- {total - shown} unread message(s) not shown (this list shows the newest {shown})`，与表头 `{total} (showing {shown})` 同一减法；查询窗口（20）之外的也计入，因此 total 超过窗口时即使预算没砍也会出现这一行。能给出的 `read_history` 调用只来自窗口内被预算砍掉的行。
- **总预算把声明行本身算进去**：先按行从新到旧收，再在声明行放不下时从最旧的已收行往回让，最新一条仍无条件保留。
- **静态块不再穷举例外**：改为「除非未读列表另有说明——它会标明被截断的消息和未展示的更旧消息，read_history 可取全文」。这一改动使静态块前缀变化一次（prompt cache 一次性失效）。

## 2026-09-11 — 未读列表结构防伪 + 总预算 + 可执行指路（PR#401 review 🟡1/🟢2-3）

- **一条消息一行不再被多段正文撑破**：行渲染收进 `_unread_row`，正文经 `body_lines`（平台层 [[../../../../../src/narranexus/platform/utils/inline_field]]，2026-09-11 从本文件 `_unread_body` 迁出，团队房 trigger 共用）布局——首行紧跟 tag，其后每行都加 `BODY_LINE_PREFIX`（`"  > "`，空行 `"  >"`），`splitlines` 覆盖 `\r`/`\u2028` 等全部换行。于是正文里写一行 `` - `[from agent_boss]` … ``、`### Unread Messages: 0` 都只会以引用形态出现，不可能落在列表自己的层级。截断标记单独成行 `"  [cut: …]"`（`UNREAD_CUT_MARKER`），这个位置正文占不到，正文里伪造的标记同样只能是引用行。预算按**消息字符**计（缩进不算），所以标记里「only the first N are shown」是真值。`(part i/n)` 前缀仍只在首行。
- **cut 标记给出确切调用**：`_read_rest_call` —— 团队房行（房间已解析）给 `read_history(team_id="…")`，私聊里真 agent 发的给 `read_history(with_agent="<agent_id>")`；`usr_*`/平台发送者且房间未解析时没有工具可接受的句柄，标记如实说「取不到，请向发送者要」，不指向做不到的调用。为此 `_room_labels` 的返回改为 `{channel_id: {"name", "team_id"}}`（name 标 tag，team_id 给指路；不把 raw `channel_id` 打回 tag）。
- **整段未读总预算** `UNREAD_SPAN_MAX_CHARS = 8000`，按**渲染后**的行计（含 tag、分片前缀、缩进、标记）：从最新一条往回收，最新一条无论多长都保留；放不下的更旧行不静默丢，而是在表头下一行声明未展示条数与 read_history 调用（口径见上一节），表头 `(showing M)` 报实际展示数。

## 2026-09-11 — 未读预览不再静默截 200 字（B-23 / upstream #73）

#73：团队房里一条三段指令到了 agent 手里只剩「... so people can scan it via」——恰好是原文前
200 字符。根因是 `_volatile_context_parts` 对每条未读 `content[:200]` 硬切且不加任何标记，
而静态块又告诉 agent「未读已在 context，不用再取」，所以被切的片段和完整消息无从区分，agent
按残片执行/追问。修法：行预算常量 `UNREAD_PREVIEW_MAX_CHARS = 1000`（装得下正常多段指令），
超出由 `_unread_preview`（现 `_unread_row`，见上条）截断**并声明**（原长、展示长、用 `read_history` 取全文）；静态块那
句补上「长未读会被截断并注明，read_history 返回全文」。`(part i/n)` 前缀照旧叠在截断文本前。

## 2026-09-09 — 未读列表给分片行加 `(part i/n)` 标签

`gather` 的未读预览当时仍是逐行 200 字（2026-09-11 起见上条）；一条分片消息的第 2 块预览会像「从句子中间开始的
消息」，所以带 `part_count` 的行前缀 `(part i/n)`。重组只在 trigger 的 turn 入口做
（[[multipart]]），这里不合并——预览是窗口，不是投递面。

## 2026-09-03 — Reply Discipline 两条加宽

「Do NOT repeat yourself」→「— or anyone else」:队友已说/已接受的不要复述回去,不要发
会话已有内容的总结;「Substance only」加「not a reply that only agrees」。
同一目标的团队房侧改动在 [[../../message_bus/message_bus_trigger]]。


## 2026-08-21 — 通信能力跟着 agent 走：合并后的 review 收尾（🟢×4）

PR#339 合并后增量 review 的 4 处 🟢 收尾（均非阻塞）：

- **承载 context 承诺的失败必须可见 → `gather` 全部同类 sink 升 `warning` 且各自独立 message**（PR#341 两轮审 Important:判据对全部成立,不能只落一部分）：`team address book fetch failed (names query)` / `team address book wiring failed (db handle)` / `known agents fetch failed (agents scan)`（静态块承诺「everyone else you can reach is in Known Agents」+ 2026-08-02 P1 第 1 步靠它）/ `unread fetch failed`（静态块说「unread 已在 context,不用再取」）/ `team room labels failed`（`_room_labels`,team 房间会被误标成私聊）/ `agent discovery sync failed`（P1 section 02 的补救）。另外 **`_get_shared_db()` 自己的 debug 升 `warning`**（`shared DB client unavailable`）——它是 4 个调用点里「db 为 None 静默跳过」这条路的唯一可见点（调用方 `if db:` 落空不进各自 except）,一处覆盖 address book / discovery / known-agents。**只改级别/加日志,不动 `except` 的 fail-open,`if db:` 不改 assert/抛出,`_room_labels` 手写 raw SQL 不碰**（改 SQL 升级成需双方言验证）。
- **patrol 注入只点名 `message_team`** → 改成 `do NOT call message_team or message_agent — no bus send tool is on the desk this turn`,与 `disallowed_tools` patrol 分支（两个都摘）对齐,让静态块那句 `unless this turn's own prompt says otherwise` 的对冲两个动词都真正被覆盖。**不写 `reply_owner`/`notify_owner`**——patrol 不摘它们（ChatModule patrol `return []`）,写进去就成假声明。改在 trigger 的 patrol 注入,不动静态块（不许按房间分叉）。
- **suppression 侧跨轮次防泄漏守卫补回**：`test_bus_expressive_declaration.py::test_suppression_reads_each_turn_on_a_reused_instance`——一个实例 patrol→bus→patrol,最后回 patrol 让「replay 上一轮 `[]`」蒙混不过。
- **命名/文档漂移**：cross-channel 锁测试改名 `test_the_block_does_not_confine_the_agent_to_the_trigger_channel`（旧名 says…available 与软化后的断言脱节）；`last_verified` 刷到 2026-08-21。

## 2026-08-20 — 通信能力跟着 agent 走，不再被触发渠道绑死

**背景（实锤事故）**：老板让 Loki 转达「以后回执发到 Arena 群，别只私聊回我」。Loki 逐个私聊群发，四个成员全都懂这条指令，却没一个发进群——两个退回 `message_agent(to=Loki)` 私聊回，一个 `tool_search("message team room send")` 搜不到 `message_team` 直接空手结束（`system_undelivered`）。根因：`disallowed_tools` 按**触发渠道**把非默认的发送动词塞进 `disallowed_tools`，而 dispatcher `visible = 频道工具 − disallowed`，于是那个动词**既不能调也搜不到**。「不是本轮默认」被实现成了「本轮禁止」。

**改动**：把「触发渠道 = 能力边界」拆成三件独立的事——①你在哪被叫醒（context 线索，不变）②你能触达谁（standing，各模块各报）③你桌上有啥（默认只有触发渠道对应的那一个 reminder）。

- `disallowed_tools`：**删掉 trigger-channel drop**。**非 patrol 轮次一律不 suppress（`return []`）——bus / owner-chat / job / IM 皆然**（无条件,不检查 `claims_source`;这与「能力跟着 agent 走」一致:owner 私聊里也能让 agent 发团队群。owner-chat 的旧防御靠的是 `expressive_tools` 不声明 reminder,那半仍在)。`message_agent`/`message_team` 双双留在桌上/可搜。唯一仍清空两者的是 **patrol**（靠说话投递、不调发送工具），保留。静态指令那句「你能跨渠道」带 **"unless this turn's own prompt says otherwise"** 对冲,才在 patrol 面（两个动词确实被摘）上不为假——byte-stable 不许按房间分叉。
- `expressive_tools`：**行为不变**——仍只声明触发渠道对应的那一个默认 reminder（省力路径 = 在被问的地方回答）。改用 `_is_team_turn` 保持 DRY。反误发不靠删工具，靠「默认省力 / 跨渠道要 search+显式 target」的意图梯度（Owner 定：误发是 agent 智能问题，随水位自愈，不加硬闸）。
- **通讯录**：新增 standing `bus_teams`。取数抽成 producer `_team_address_book(db, team_ids)`（对标 `_room_labels`：producer 与 renderer 分开，好单测），hook 传入 known-agents 已解析的 `my_team_ids`——**成员关系不二次查询**，producer 只多读一次 team 名称。`_volatile_context_parts` 渲染「### Your teams」带 `team_id`。补掉第二个盲区：DM 轮次里 `bus_room_labels` 只给触发窗口内的房间，`message_team(team_id=…)` 之前没地址。cap `MAX_TEAMS_IN_CONTEXT=30` **在 fetch 侧就截**，且 `sorted(team_ids)` 后再截（超 cap 时哪些团队存活可复现，不看查询行序）。地址簿放在**自己的 try（步骤 2b）**、`my_team_ids` 提到 known-agents try 外：known-agents 段末尾的 `agents` 全表扫描抛异常也不会连带丢掉通讯录。测试 `test_bus_address_book.py` 覆盖三层：producer（真 db，删 producer body 变红；含 except→`[]`、cap）、hook 装填（membership→producer→extra_data，删 hook 调用变红）、renderer（含 cap 与 heading 一致）。

**文案扫一类（本文件三处 + 兄弟文件）**：静态指令「exactly ONE of these two calls per turn / a fresh one will have that call」改成「The conversation that woke you is only where a plain reply goes by default … you are not confined to it. Unless this turn's own prompt says otherwise …」（`unless…otherwise` 对冲让它在 patrol 面也为真）；`disallowed_tools`/`expressive_tools` docstring、`BUS_TEAM_ROOM_EXTRA_KEY` 注释（在 trigger）、「## Answer the peer」注入（在 trigger）、被删 channel-list 的注释一并改。测试锁：`test_visibility_wording.py::test_the_block_does_not_confine_the_agent_to_the_trigger_channel`（2026-08-21 改名）、`test_bus_expressive_declaration.py`（desk 系列重写）、新 `test_bus_address_book.py`。删掉任一处代码 → 对应测试变红。

**ChatModule 未动**：它的 reply_owner/notify_owner drop 是同一个 party（owner）的两种 register（同步 chat vs 异步 inbox），是模式相关而非「另一个会话」，且 bus 轮次本就保留 `notify_owner`——留待后续统一时再评估。

## 2026-08-19 — after_turn 复用 primary_room_of

`replied_teams → 房间 channel` 的解析从手写第五份 `created_by==team_<id> marker` 查询改为调 `team_rooms.primary_room_of(db, tid)`（marker 组合的唯一来源）。行为等价（`primary_room_of(None,...)` 内部 try/except 返回 None，保留旧「拿不到 db 就跳过」语义），去掉了会随 `primary_room_of` 演进而漂移的副本。

## ⚠️ 改这个文件的文案之前，先读这条（常青，不随条目滚动）

**`_static_instruction_parts()` 是逐轮字节稳定的、且对"这轮在哪种房间"一无所知。**
它进的是可缓存的系统提示前缀（R4），每一个 bus-enabled 轮次都发——owner 私聊、job、
各 IM 渠道、peer DM、team 房间，一字不差。由此有两条硬规则：

1. **里面每一句都必须在它到达的每一个 surface 上成立。** 只在某类房间为真的话，写进去
   就是在同一个上下文窗口里制造一对矛盾——而房间自己的 turn prompt 就在几十行外，说着
   相反的话。
2. **不许按房间类型分叉。** 分叉毁掉它字节稳定的意义（2026-08-12 定的调子）。正确做法是
   **把话改成处处成立的说法，把房间特有的事实交给房间自己的 prompt**——它是唯一知道
   答案的地方。

推论，同样容易踩：

- **别从"某个标记不在"反推这轮从哪来。** 本块只管 bus，它看不见别的触发源。这一条上
  栽过两次（一次说"没标签 = 来自 owner 主聊天窗口"，IM 轮次为假；一次说"没标签 = 不是
  从 bus 来的"，100% 的 bus 轮次为假）。
- **写"XX 会出现在某处"之前，先确认它真的会出现。** 这里曾有一整节规则指着一个从未
  执行过的输入前缀（见 2026-08-17 条目）。
- **改完扫一遍同一份文案的其它拷贝**：文件头 `@description`、方法 docstring、MCP 工具
  docstring（那些也进模型上下文）、以及本 mirror 里的旧条目。同一句话在本文件出现三四份
  是常态，这个 PR 连着四轮每轮都漏掉一份。
- **删一条断言就要问它守的是什么**；`tests/message_bus/test_visibility_wording.py`
  存在的唯一目的就是让上面这些句子不能被悄悄改回去。

## 2026-08-17 — 指令块整体重写成「两种社交处境」，以及我重写时丢掉的那些保证

工具面从 19 个收到 13 个并全部改名（`bus_send_message`→`message_team`、
`message_agent` 合并进它、新增 `message_team`、`bus_*_team_*`→`team_*`、
`work_*`→`team_work_*`），指令块随之整体重写：不再教一个叫 MessageBus 的子系统，只教
**两种处境**——和某个同伴私聊、在某个 team 房间里。

**声明面此前声明的是两个已经不存在的工具**（`message_team` / `message_agent`）。
这是「声明死工具就是对模型的错误信息」最严重的形式。现按 surface 给**唯一动词**：
team → `message_team`，peer → `message_agent`。新增
`test_exactly_one_verb_per_surface`——那条不变量是整个改造的立论，此前无人守。

`disallowed_tools` 已覆写但**目前返回空**：基类钩子不收 `ctx_data`，拿不到本轮的
team 标记。docstring 明写了这一点，所以 spec §4.5 的桌面表**尚未完全强制**——声明是目前
唯一的收窄手段。别读成已经做完。

### 一次自己犯的 P11

整块重写时我**丢掉了六轮评审建起来的好几条保证**，它们守的危害一条都没消失：

- **「不 @ 就没人醒」** —— 我只写了「@ 会叫醒谁」，丢了那条承重的逆命题
- **「读懂给你的东西」整节** —— 标签形状（由 `_bus_tag` 生成）、`User` = 人、
  `[system]` = 平台、以及**不许从标签有无反推本轮来源**（P6 栽过两次的那条）
- **沉默的作用域** —— 「make no call at all」会被过度解读成连 owner 都不许告知
- **交付规则指向何处** —— 只说「through the call」太含混

都已补回。**教训与 PR #311 那次同源**：这个文件的守卫是为了让某些句子不能被悄悄改回去，
而「整块重写」正是最容易连守卫一起抹掉的动作。

### 断言改成锁保证，不锁旧字面量

`test_visibility_wording.py` 里 12 条因措辞变更而红。**一条没删**，全部改成锁住**保证**：
"resurface" 变 "comes back" 但作用域检查保留；交付规则**不再要求点名工具**（点名就违反
P1——现在一个 surface 一个动词），改为断言它指向本轮；「双发禁令」那条随「纯文本自动上墙」
一起退役，替换为「本块不得点名任何 surface 专有工具」。跨文件契约测试跟着新承诺走
（从「替你上墙」变成「房间必须点名 `message_team(`」）。

### 一个功能 bug：改名让读游标死锁

`after_turn` 靠在 trace 里匹配 `message_team` / `message_agent`
判断「这一轮回复了吗」，据此推进 `last_read_at`。两个工具改名后它**仍在匹配旧名**，于是
**什么都不算回复、游标永不推进**——正是刚在 IM inbox 侧修掉的那种永久未读死锁，被一次改名
在 peer 侧重新引入。现在匹配 `message_agent` / `message_team`，后者还要把 team 解析成房间
频道。守卫是 `test_get_unread_contract` 那条（它自己也带着旧工具名，所以此前看不见）。

### 词表：`MessageBus` 从 agent 可见文本清零

未读列表标签 `[MessageBus · …]` → `[from …]`；turn context 标题
`### MessageBus — Current State` → `### Who is around, and what is waiting`。日志与
docstring 里的实现名保留（agent 看不到）。
## 2026-08-16 — 静态段里最响的那句，正是 team 房间逐字反驳的那句

2026-08-12 那轮把「只看得到 @ 你的消息」和「未回复会重现」改成了处处成立的说法，
**但漏掉了同一段里语气最重的一条**——而它恰恰是矛盾最尖锐的一条：

> "Finished work is never ping-pong... send the result via `bus_send_message`。
> **纯文本收尾 = 零交付，对方永远看不到**"

在 team 房间里这句正好相反：纯文本**就是**回复（[[message_bus_trigger]] 的
`_deliver_reply` 替它上墙），而 turn prompt 明写「**禁止**用 bus_send_message 投递
本条回复，否则双发」。两句话同一个上下文窗口，讲同一件事，结论相反；而静态段那句
因为背着 8/1 briefing squad 的 P0，语气被反复加重，**是更容易赢的那一句**。

修法沿用 8-12 的既定套路（不分叉、说处处成立的话）：**保留义务，交出机制**。
「干完的活必须 REACH 对方」是普适的，怎么送由这一轮的 surface 决定——给了投递工具
就用工具，turn prompt 说了替你上墙就写纯文本。两条路都点名，因为两条路都真实存在。

同批扫掉的另外两处（铁律 #8——上一条 mirror 记的教训就是"修一份留一份"）：

- **「Just stop the turn」**：那是一条关于**工具调用**的规则，只在"不调工具就等于
  没输出"的 surface 上等于沉默。会自动上墙的 surface 上，残留的纯文本仍是一条消息。
  改成「不调工具**且**不留任何回复文本」，并说明沉默是"什么都不产出"而非"产出得短"。
  顺手删掉尾巴「unread cursor advances appropriately」——同一个游标下面两行的
  resurfacing 规则已经讲得又准又有作用域，这是第三份含混说法。
- **`[MessageBus · …]` = "另一个 agent，不是你的 owner"**：这个标签标的是**路由**，
  不是发送者的物种。team 房间里用户自己以 `usr_<id>` 发言，会进未读列表（当时也以为
  会进 input 源标签——**2026-08-17：那条路从未执行，已退役，见下**）——于是**老板本人说的话被声明为"这是台机器"**，还紧挨着一条"跳过寒暄"。
  改成「usually another agent，但**人也会在 bus 上说话**，读标签里的发送者，别默认是机器」。

配套 `_render_sender()`：`usr_*` 在未读列表里渲染成 `User`。
「读发送者」这条指令，只有发送者可读时才成立，而 `usr_a1b2c3` 对模型什么都不说。
命名与 [[message_bus_trigger]] 的 `_sender` 一致——同一行数据，两处露出，一个叫法。

`USER_SENDER_PREFIX` 从 `schema.team_schema` import（不是重打字面量）：该前缀全仓
唯一定义点的约定见 [[team_schema]] 2026-08-11 那两条。

**预审第二轮补掉的（同一 PR 内）**——每一条都是"扫得不够远"，正是上一条 mirror
记的那个教训本身：

- **物种断言有第二份，还带着行为。** 删掉了 Source Recognition 里的「不是你的
  owner」，却把十四行之下的「**The other party is another agent, not a human.**
  Skip pleasantries」留在原地——那是带**后果**的那一半，它自己就把断言重新立了起来。
  改成「**当**对方是 agent 时…；当发送者是人时，就当人来说话」。brevity 那半句背着
  ping-pong 的 P0，保留不动。
- **两个分支同时成立的规则不是规则。** 初稿写的是「给了投递工具就用工具；说了替你
  上墙就写纯文本」——而 team 轮次上**两个前件都为真**（team gate 只清空
  expressive **声明**，工具 schema 仍在上下文里，因为本模块没有
  `disallowed_tools`），句子又没给优先级，模型能直接照着工具列表走的恰好是双发
  那条。改成**例外式**：team 那条放最前、写成禁令，工具那条做兜底。
- **`[MessageBus · …]` 格式有三份，且示例是错的。** 示例写四段（display name +
  id），两个渲染点都是三段。以前只是装饰，现在规则要求"读标签里的发送者"，它就成了
  承重件。抽出 `_bus_tag()`，示例由同一个函数生成，两者再也漂不开。
- 沉默那条补上作用域：「对 **bus 对话** 不留任何回复文本」——四十行外还挂着"绝不压制
  对 owner 的回报"，无限定的"不留任何文本"会把人在等的答案一起吞掉。

### 第三轮（预审 Critical）—— input 源标签**从来没有存在过**

`gather` 第 5 步那段"给输入加 `[MessageBus · …]` 前缀"的代码，**两处
断链，一次都没跑过**：

1. 它 gate 在 `extra_data["working_source"]` 上——**全仓没人往这个键写**。
   `working_source` 是 ContextData 的**字段**（`context_runtime.py:147` 播种），而
   `extra_data` 只装 `trigger_extra_data`。正确读法在同一文件往上八行
   （`expressive_tools` 用 `working_source_matches(ctx_data.working_source, …)`）。
2. 就算 gate 过了，它写的是 `extra_data["input_content"]`——**全仓唯一的读者就是它
   自己那两行**。模型真正收到的是**字段** `ctx_data.input_content`
   （`context_runtime.py:1032`）。

于是这一段里**每一条**指着"输入开头的标签"的规则都指向空处，包括第二轮我刚写的
「没有标签 = 不是从 bus 来的」——**它在 100% 的 bus 轮次上为假**（第一轮那句在 IM
轮次上假，第二轮把假搬了个位置，正是 PR#260 那个模式）；还包括"替 owner 去问另一个
agent"剧本第 4 步的识别信号，也就是唯一把答案回报给 owner 的那一步。

**选择退役而不是接上（铁律 #2：删干净，不留 shim）。** 接上是**两个已发布 prompt 的
行为变更**——team 轮次的 input 就是整段 `[Team Group Chat]…`，DM 轮次是
`_build_prompt` 的输出，两者都已经用自己的话交代了发送者，在前面再糊一个机器前缀是
产品决策，不是 bug 修复。所以：

- 删掉该分支，原地留注释**点名两个死键**，防止下一个读者好心把它接回来；
- 这一段规则改为描述**真正到达模型的那个标签面**——本模块 turn context 里的
  **Unread Messages** 列表；
- 「没有标签」那条不再做任何推断：标签描述的是**未读队列**，本轮由什么触发是**本轮
  prompt 的事**，本块不得从标签的有无反推（两轮都栽在这个反推上）。
- 第二轮的 I4（按 `bus_channel_id` 过滤）连同它的测试一并移除——那是在一条从未执行的
  分支上做的正确性工作。

**连带升级（I8）：退役后未读列表成了规则唯一指向的标签面，它的正确性权重上升。**
该列表没有 `msg_type` 过滤（未读谓词也没有），所以 patrol / stop / 公告栏通知对每个
成员都是未读、都会落进来：
- `team_<id>` 发送者被原样打印 = 凭空造出一个队友，而 `message_bus_trigger._who`
  正是为此拒绝原样打印它；
- 公告栏通知由 UI 发出时**不记 actor**，`from_agent` 是 **owner 的 `usr_` id**、
  `msg_type` 是平台类型——只看发送者会渲染成 `User`，**而本轮新写的规则恰好说
  "`User` 是个人、要当人说话"**，等于把平台的话署名给了老板。

所以 `_render_sender` 现在收 `msg_type` 且**类型优先于发送者**，平台行统一渲染成
`[system]`，与 `_build_team_prompt` 给同一批行打的标签一致。`_bus_tag` 的第三个参数
带默认值，指令里那个示例仍可由两个字面量构造。

**评审管线补的一刀（同一 PR 内）**：上面这句「与 `_build_team_prompt` 一致」当时**只是
一句注释**——`[system]` 在两个文件里各打了一遍字面量，没有共享常量、也没有任何测试守它。
**而这正是本 PR 在 mirror 顶部立下「同一份文案的其它拷贝必须一起扫」那条不变量之后，
自己新增的一份未守卫拷贝。** 已把 `SYSTEM_SENDER_LABEL` 搬到
[[system_messages]]（那个文件的 header 讲的恰好就是这个论证：别让字面量成为知识的存放
地），两处 import；两边**只共享名字、不共享格式**（trigger 是行前缀，本模块是 `_bus_tag`
里的发送者字段）。三条原本硬断言 `"[system]"` 的测试改为断言常量本身——否则重命名时它们
会以"守卫"的姿态挡住正确的改动，漂移时反而沉默。另加一条**跨文件**测试，断言
`_build_team_prompt` 的 scrollback 与未读列表对同一条 patrol 行用同一个标签。

同批清掉三处 Minor：`unread_models` 是删掉第 5 步后的残留别名（唯一真实消费者就是被删的
那一步），已折掉；`_render_sender` 的 docstring「one row, two surfaces, one name」**说过头
了**——只有 `usr_*` 和平台行两边一致，普通 agent 在 trigger 侧走 `member_map` 渲染成显示名、
在这里保留原始 `agent_id`（那是 `bus_send_to_agent` 的入参），已如实收窄；静态段那句
「你的 Unread Messages 列表…」改成条件式，因为零未读时该小节根本不存在——这条正是本 PR
自己立的「写『XX 会出现在某处』之前先确认它真的会出现」。

测试：`tests/message_bus/test_visibility_wording.py`（该文件的立意就是"静态段的每句话
必须在它到达的每个房间里成立"）现共 22 条。其中**跨文件契约**一条：断言
`_build_team_prompt` 确实做了本模块规则所依赖的承诺（"替你上墙" + "禁止用投递工具"）
——此前无人守，trigger 那边一次改词就能把本模块的规则重新变成谎话。另有一条断言那段
死分支是**被删掉**而不是留着不可达（只看代码行、跳过注释，因为留下的注释故意点名了
两个死键；断言用**裸标识符**，否则加个默认值参数就能绕过）。

**并且，本轮一度自己弄丢了三条测试。** 删 I4 测试时是按两个函数名之间切片做的，切走
了夹在中间的三条无关测试，其中两条的断言**无人接手**：一条守"标签不声称发送者是机器"
（**本 PR 第一个 commit 的第 3 项头牌修复**），一条守沉默规则（"Just stop the turn" 的
退场，以及第二轮 M1 收的作用域）。而当时的 commit 与本条目都写着"18 条"，读起来像增长。
两条已恢复并在文件里标注了来龙去脉。教训与前两轮同源：**这个文件本身就是为了让某些
句子不能被悄悄改回去而存在的，它的守卫少了两个而没人发现，比句子被改回去更糟。**

`tests/module/test_a2a_ask_another_agent_guidance.py` 里那条 `assert "plain text"`
**锁的是机制**，随之改为锁义务（`has to REACH them`）——它写的时候机制还是普适的，
team 房间出现后就不是了。义务本身仍然被钉住，P0 没有松（机制断言并未丢失，而是搬到了
`test_the_delivery_rule_defers_the_mechanism_to_the_surface`）。

**未修、留作独立改动的**（都不是文案层）：① team 轮次没有屏蔽 bus 投递工具
（`disallowed_tools` 这个钩子存在但本模块没实现），文案劝阻 ≠ 工具消失；
② 级联上限 `MAX_TEAM_AGENT_HOPS` 只实现在 `_deliver_reply` 一条路上，
`bus_send_message` 直接写 team 房间不受任何计数约束；③ `get_unread` 不排除当前
team 房间，房间消息在 scrollback 之外被二次渲染。

### ⚠️ 这一轮**没有**让 team 房间的矛盾归零 —— [[chat_module/prompts]] 里还有更大的一份

`CHAT_MODULE_INSTRUCTIONS` 无条件进系统提示（`chat_module.py:232` → 基类
`contribute_instructions`，**没有任何 team gate**；全仓消费 `BUS_TEAM_ROOM_EXTRA_KEY` 的
只有 `context_runtime` 和本模块）。它说的是：

> "Your plain text output is your **private self-thinking** — the user CANNOT see it"

而且不是一句，是**一个 CRITICAL 小节 + 一张可见性表格 + 一个"你在隔音房间里"的比喻**，
反复三遍。**它比本轮删掉的任何一句都长、都响。**

所以本轮的改动 #1 #2 在它们真正针对的那个 surface 上**并未达成目的**：team 房间里的
agent 仍然被告知写纯文本等于零交付，只是这次说这话的是 ChatModule 而不是本模块。
`context_runtime.py:1178-1184` 说明这个问题在**回复提醒**那一层已经被认到并处理了，
但只停在了那一层。

不在本轮修，是因为它属于 ChatModule 自己的文案（铁律 #3），而且那是全产品最吃重的
一段 prompt，动它该走独立 PR 独立评审。**但不要把本条目读成"矛盾已解决"** —— 本轮
只拆掉了 bus 模块那一半。这一条比上面三条"未修"都大。

## 2026-08-10 — 这台 MCP server 上挂了第二套工具族

`create_mcp_server` 除 `_message_bus_mcp_tools` 外,还注册
[[_work_board_mcp_tools]](5 个工具:add / list / claim / complete /
update_status)。

**挂同一台 server**:工作项的作用域是 team **房间**,而房间就是一个 bus
channel —— 能在房间里说话的 agent,恰好就是该维护这块板子的 agent。独立 Module
要新端口、新 instance 生命周期,还得反过来查 bus 的表(铁律 #3)。

但它有**自己的状态机和自己的写入边界**(`stalled`/`paused`/`cancelled` 模型不
可写),所以分文件。只读本文件会以为这台 server 上只有消息工具。

## 2026-08-05 — 指令不再把模型送去调一个会破坏名录的工具（review）

「When NOT to Call Tools」里那句 `Do NOT call bus_register_agent unless your
profile needs updating` 有两重问题：工具本身已删（见
[[_message_bus_mcp_tools]] 同日条），而且原文恰恰在**「想更新 profile 时」**
把模型指向它——那正是它会把 `owner_user_id` 写空、让自己从同 owner 搜索里消失
的路径。改写成「平台没有注册工具；要改同伴看到的内容用
`update_agent_profile`（Awareness），capabilities 是推导的、不能自报」。
有测试断言指令里不再出现旧工具名、且出现新工具名。

## 2026-08-04 — 名录写入交给统一 seam；Known Agents 不再打印占位符

两处（P1 段02）：

1. `gather` 里那段内联注册（硬编码 `capabilities=[]`、把
   `agent_description` 原样当描述发布）换成调 [[agent_discovery_sync]] 的
   `sync_agent_discovery`。那段代码是"`bus_search_agents` 对任何查询都返回空"
   和"配置好的 agent 被报成待配置"的直接原因（prod 全表 488 行）。现在这里只是
   **每轮的幂等兜底**——真正的注册发生在创建/配置那一刻（[[auth]]、
   [[awareness_module]]、[[install_pipeline]]）。
2. `_volatile_context_parts` 的 Known Agents 渲染：描述判定为 unset
   （[[entity_schema]] 的 `is_agent_description_unset`）时**整段不渲染**，而不是
   把占位符打出来。每一行都写着同一句"待配置的新 agent"，等于告诉发问的模型
   「这些同伴都不可用」——owner 说"问问教学专家"时它无从下手。

## 2026-08-04 — bus 轮次的回复面声明（origin-aware）+「干完活必须交付」纪律

P0 recvrdLPavENwg（8/1 briefing squad：5 个分析师真研究、纯文本收尾、零交付）
的声明侧修复。新增 `expressive_tools(ctx_data)` 覆写：**只在**
working_source=MESSAGE_BUS 的轮次声明 `bus_send_message` + `bus_send_to_agent`
（fully-qualified，派生自 mcp_server().server_name）。三重门：
① 非 bus 轮不声明（chat 轮广告 bus 工具会诱导经 bus 回 owner）；
② team 房（extra_data `bus_team_room`，由 [[message_bus_trigger]] 盖章）不声明——
纯文本自动上墙、prompt 禁投递工具，声明会诱导双发；③ 无 ctx 不声明。
配套 `claims_source`：收集点（[[context_runtime]]）把来源模块的声明排
到最前，默认回复工具从此跟着「谁联系的你」走。

Reply Discipline 同批加一条「**Finished work is never ping-pong — deliver it**」：
沉默许可只给"没实质内容"，做完别人求的活必须用 bus 工具送达，纯文本收尾
= 零交付。

> **2026-08-17 更正**：「纯文本收尾 = 零交付」这半句**已撤回**——它在 team 房间里
> 正好相反（纯文本就是回复，turn prompt 明禁投递工具）。**留下的是义务**（结果必须
> REACH 对方），**交出去的是机制**（由这一轮的 surface 决定）。见本文件 2026-08-16 /
> 08-17 条目。别照着这段把旧措辞写回去。

注意：与 2026-08-01 那条同理，文案对弱模型效力有限，真正的机制
修复是声明面（本条）+ 判定面（message_bus/__init__）对齐。

## 2026-08-01 — 指令新增「替 owner 去问另一个 agent」剧本

P1 段 06:owner 说"问问教学专家在干嘛",agent 答做不了。能力一直都有
(`bus_send_to_agent` 会触发对方),缺的是**把这类请求认出来并给出路线**。
新增小节明确:① 这类请求你能做,**不得回答无法联系其他 agent**;
② 从 Known Agents 取准确 id;③ 用 `bus_send_to_agent` 发问,
**别用社交网络/联系方式工具**(那返回联系方式,不是答案);
④ 告诉 owner 已问、回复会另开一轮;⑤ 对方回复到达时用
`send_message_to_user_directly` **回报给 owner**——并写明
Reply Discipline 只管对**同伴**的回复,绝不压制对 owner 的回报
(不写这句,那条"没实质就沉默"的规则会把用户要的答案吞掉)。
找不到目标要问清楚,那是澄清问题、不是拒绝。

文案进 `_static_instruction_parts`(静态、逐字稳定,可缓存),有测试断言
稳定性与各条要点。

Reply Discipline 同批加了一条「问题从来不是 ping-pong,必须回答」——含
「替 owner 转达的问题」和「回报自己 owner 不算交差」。**但要知道:光加这
条文案对被测模型无效**(真机 3/3 仍拒答),真正起作用的是
[[message_bus_trigger]] 那侧把假的 Owner Relay 指令换掉。这条文案保留是
因为它本身正确、且对强模型有用,**不要**把它当成该问题的修复。

## 2026-07-28 — R4b：三个数据列表搬进 contribute_turn_context

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

`contribute_instructions` 原本 = 使用规则 + Known Agents / Your Channels / Unread
Messages 三个列表；unread 每轮消费必变、另两个被 bus 工具会话中途改变
（prod 稳定性 11/17）。现拆为：

- `_static_instruction_parts()` — 使用规则（仅烘焙 self.agent_id，会话内恒定）。
- `_volatile_context_parts(ctx_data)` — 三个列表，渲染逻辑（MAX_* 上限、顺序、
  文案）零改动。
- `contribute_instructions` — flag 开 → 只拼 static（轮间字节稳定）；关 → static +
  volatile 同块拼接（legacy 逐字节一致）。
- `contribute_turn_context` — `### MessageBus — Current State` 稳定标题 + 三个列表；
  三个列表全空 → ""。

"unread messages are already injected into your context automatically"（规则
段 :182 附近）的表述依然成立——注入位置变了，行为没变。

第 4 步 "Fetch channels" 的查询原本 `ORDER BY c.updated_at DESC`，但
`bus_channels` 表从来没有 `updated_at` 列（schema_registry 里只有
`channel_id/name/channel_type/created_by/created_at`，且 local_bus 建频道时
也只写 `created_at`，无任何代码维护 `updated_at`）。SQLite 抛
`no such column: c.updated_at`，被 `except` 吞成 `logger.debug`，结果
`bus_channels` 上下文静默缺失。改为 `ORDER BY c.created_at DESC`（表中唯一
存在的时间列）。同类事故的旁证见 [[message_bus_trigger.py]] :497 的注释。

## 2026-05-19 — Reply Discipline 段强化 Agent-to-Agent 简洁优先

新增两条规则：
1. 显式标注"对方是 agent 不是 human"，要 Agent 跳过寒暄、首选一句话 /
   单数字 / 单 list 这种最小回复形态。
2. "Substance-empty → 明确选静默" — 没新信息时不要 call `bus_send_*`，
   直接结束这轮；平台按 `[NO_REPLY]` 处理，unread 游标按正常方式推进。

   > **2026-08-17 更正（上面两条都已改，别照着写回去）**：
   >
   > 第 1 条的「**显式标注"对方是 agent 不是 human"**」已**收窄成条件式**——team 房间
   > 里 owner 本人就在 bus 上说话（`usr_<id>`），无条件的物种断言会把"跳过寒暄"对准一个
   > 人。现写作「**当**对方是 agent 时…；当发送者是人时，就当人来说话」。最小回复形态
   > 那半句（brevity）背着 ping-pong 的 P0，保留。
   >
   > 第 2 条的「直接结束这轮」只在"不调工具就等于没输出"的 surface 上等于沉默——会自动
   > 上墙的 surface 上残留纯文本仍是一条消息，故现在写作「不调工具**且**不留任何回复
   > 文本（对 bus 对话）」。「unread 游标按正常方式推进」这条尾巴已删除：同一个游标下面
   > 两行的 resurfacing 规则讲得又准又有作用域。


跟 [[prompts.py]] (chat_module) 配对：chat 路径强调"对人要温暖"，bus
路径强调"对 agent 要极简"。两边各自收紧自己的边界。

# message_bus_module.py — MessageBus Module 主体

## 为什么存在

`MessageBusModule` 是 `XYZBaseModule` 的子类，遵循 Module 热插拔协议。它负责两件事：在每次 AgentRuntime 执行前（`gather()`）把 MessageBus 的状态（未读消息、频道列表、已知 Agent）注入上下文；在 MCP 服务器里暴露 MessageBus 操作工具供 LLM 调用。

如果没有这个 Module，Agent 就对 MessageBus 的存在毫无感知——不知道有新消息，也不能主动发消息或管理频道。

## 上下游关系

**被谁加载**：ModuleService 根据 `module_registry` 在 AgentRuntime 初始化时按需加载；MCP 服务器通过 `module_runner.py` 启动时实例化。

**调用谁**：实例化一个 `LocalMessageBus`（通过 `get_db_client()` 取 backend）；调用 `_message_bus_mcp_tools.py` 里的工具函数暴露 MCP 工具；在 `gather()` 里调用 `bus.get_unread()`、`bus.get_channel_members()` 等取数据。

## 设计决策

Instance 级别是 **Agent-level**（`is_public=True`），即每个 Agent 有一个全局共享的 MessageBusModule 实例，不是每个 Narrative 各自一个。这是因为 MessageBus 是 Agent 级别的通信能力，不需要按 Narrative 隔离。

未读列表里每一行以 `[MessageBus · {sender} · {channel}]` 开头（类似 Matrix 的
`[Matrix · ...]` 前缀）。**2026-08-17 更正**：此处原先写的是"`gather()`
注入的**消息**以该前缀开头"，并声称 `continuity.py` 的 `_extract_core_content()`
依赖它、改格式要同步改它——**两句都不成立**。给**输入**加前缀的那段代码从未执行过
（见同日条目，已删除）；`continuity.py` 在本分支不存在，全仓也没有任何解析该标签的
消费者（`git grep '\[MessageBus'` 只剩生产端和文案）。格式的唯一定义点现在是
`_bus_tag()`，指令里那个示例由它生成，所以文案与实现不会再分叉。

~~在 `WorkingSource.MESSAGE_BUS` 触发路径下，`gather()` 注入的信息会更精简。~~
**2026-08-11 更正:这句是反的,而且从来没实现过。** `gather` 里没有任何
`working_source` 分支 —— Known Agents / Your Channels / Unread Messages 三份列表对
**每一个**场景一视同仁地注入,包括 owner 私聊、job、以及各 IM 渠道的轮次。本文件里
唯一读 `working_source` 的地方只用来给 input 加 `[MessageBus · …]` 源标签。

> **2026-08-17 更正（重要，别照着删代码）**：上面这句现在两半都不成立。给 input 加
> 源标签的那段**从未执行过、已删除**（见同日条目）。本文件如今读 `working_source`
> 的是 `claims_source` → `expressive_tools`——**那是活的**，它让 bus 轮次
> 的默认回复工具跟着"谁联系的你"走，正是 2026-08-01 briefing squad P0（只声明
> owner-chat 工具导致干完的活落进 owner 窗口、求助者永远收不到）的修复。看到"源标签
> 分支已删"就顺手清 `working_source` 读点的话，会把那个 P0 重新打开。

这句反话的代价是它掩盖了真实形状:团队房间的未读因为读游标死锁而无限堆积,再被原样
灌进该 agent 所有场景的上下文。游标已在 2026-08-11 修复(见 `local_bus` 与
`message_bus_trigger` 的同日条目),注入范围本身保持不变 —— 那是「顺带瞥一眼群里
动静」的能力所在,污染是死锁的症状,不是注入设计的错。

## Gotcha / 边界情况

`MESSAGE_BUS_MCP_PORT = 7820` 是该 Module 的 MCP 服务器端口，如果其他 Module 使用了这个端口会发生冲突。新增 Module 时注意检查端口占用。

Module 实例是 Agent-level 的，但 `gather()` 运行时的 `agent_id` 来自 `ctx_data.agent_id`——同一个 Module 实例可能为不同的请求提供服务，不要在实例变量里缓存 agent_id 相关的状态。

## 新人易踩的坑

`MessageBusTrigger`（外部驱动 Agent 处理消息）和 `MessageBusModule.gather()`（Agent 主动查询 bus 状态）是两个独立的机制，可以同时工作。不要误以为开启了 Module 就不需要跑 `MessageBusTrigger`——前者是"Agent 主动感知 bus"，后者是"bus 主动推送消息给 Agent"。

## 2026-08-11 — 未读注入:窗口取最新、总数单独查、源标签取对头

> **2026-08-17**：本条末尾那段「源标签取对头」（`unread_models[0]` → `[-1]`）描述的
> 是 input 源标签分支。该分支从未执行过，已删除；这一段仅作历史记录，不要据此推断
> 今天还有输入前缀。

抓取改为把上限**下推进查询**并取**最新** N 条。此前是拿全量再 Python 切片,切的是
oldest-first 列表的头部 —— 拿到的是积压里最古老的那些;再叠加 team 房间读游标永不
推进,这个窗口是**冻结**的:同样 20 行,一轮又一轮,以"房间当前状态"的名义呈现。

`bus_unread_total` 是新的 extra_data 键:查询加了 LIMIT 之后,`N unread (showing M)`
里的 N 不能再是结果的 `len()`,否则 N 恒等于 M。

`unread_models[0]` → `[-1]`:那行注释写着 "most recent trigger",而列表是 oldest-first,
`[0]` 是积压里**最旧**的一条。注释和代码指着相反的两端。

## 2026-08-12 — 两句站不住的规则,和一个终于有人读的字段

**「群聊里你只看得到 @ 你的消息」被改写。** 这句在 `_static_instruction_parts` 里,
对自建 bus 群是**对的**,对 team 房间是**错的** —— 后者的 turn prompt 带着整段房间
scrollback,还在十行后明说"每个成员都看得到本房间每条消息"。同一个上下文窗口里两句
互相矛盾的话。

不能加房间类型分支:这一段需要跨轮字节稳定(R4 缓存),分叉就毁掉它存在的理由。所以
改成**在所有房间都成立**的说法:「@mention 决定谁**被唤醒**,不决定谁**看得见**;
你在某个房间能读到什么,由那个房间自己的 prompt 说明」。房间的事实交给唯一知道答案
的地方。

**「未回复的消息会重新出现」收窄到私聊。** 在 DM 里未读列表就是队列,不回确实等于
延后。而 team 房间靠渲染投递,一轮跑完就算已读(2026-08-11 的游标修复),不收窄就是
一条平台在 agent 加入团队那一刻起就不再遵守的承诺。

**`via_team` 终于有消费者。** 它被算出来后全仓无人读。Known Agents 这份列表把队友和
owner 名下其它所有 agent 混在一起,agent 想找人帮忙时分不清"已经和我在一个房间里"
和"素不相识、要冷启一条 DM"。现在渲染成 `(teammate)`。

## 2026-08-12 (review 后) — 被推翻的那句话在同一个文件里还有第二份

静态段那句已经收窄成「**未回复的私聊**会重新出现」,但一百行之后的 volatile 块仍然
无条件输出「Ignored messages stay unread」—— 而它就贴在 `### Unread Messages` 的表头
下,那个列表里**混着 team 房间的未读**。对 team 房间它现在是假的(跑完一轮就推进
`last_read_at`,回不回复都一样)。

修一份留一份,正是这次改动开篇要消灭的「同一个上下文窗口里两句矛盾的话」,只是位置
挪了一百行。铁律 #8 说的"加功能时顺手扫一遍相邻代码",这次没扫到。

## 2026-08-18 — `disallowed_tools(ctx_data)` 签名同步

跟随 [[base.py]] 2026-08-18 的接缝修复：压制 hook 改读本轮自己的 ctx，不再依赖声明 hook
留下的实例状态（`_last_ctx` 已删）。收集环先压制后声明，旧写法在全新实例上必然误判。

## 2026-08-18 — 从 agent 词汇里清掉 channel：删列表、消息标签改用团队名

`### Your Channels` 块把裸 `channel_id` 和 `channel_type` 印进每一轮上下文 —— 正是本次
改造要拿掉的词汇（spec §3.1「没有 channel」/ §8 验收标准）。它存在的唯一理由是让
`read_history(channel_id=...)` 可调用；该工具改成按把手取（`with_agent` / `team_id`，见
[[_message_bus_mcp_tools.py]]）之后，这个列表没有读者了，连带 `gather` 里
那条每轮白跑的 top-20 查询和 `MAX_CHANNELS_IN_CONTEXT` 一并删除。

更硬的一处是 `_bus_tag`：它的第二个字段原本是 `channel_id`，出现在**每一条**未读消息行上，
比那个列表更不可回避。现在是团队自己的名字，且只在团队房间出现 —— 私聊里 sender 就是
conversation，第二个字段会是同一事实说两遍。空标签渲染短形式。

标注来源 `_room_labels`：只解析未读**窗口**里出现的房间（有界，且每一行都会被打印，与被删
的 top-N 列表相反），永不抛异常、永不猜测 —— 解析不到就缺席、退回私聊形式。把私聊误标成
房间比缺标签更糟，因为两者的回复纪律不同。

守卫：`test_visibility_wording.py` 的「示例必须由渲染同一个函数生成」保留，改为同时断言
两种形态并禁止 `ch_yyy` 出现；`test_turn_context_split.py` 新增团队名形态的覆盖，且故意
继续喂 `bus_channels` fixture —— 停止喂它会让「渲染器已丢弃」的断言变成空断言。


## 2026-08-18 — 静态块的两句假话（预审 I10/I11）

块是字节稳定的（R4 前缀缓存），因此不能按轮次分支，而它到达**每一个** turn —— 包括
owner 聊天轮和 job 轮。两处因此站不住：

1. 开篇「每轮开头会告诉你在哪种处境、该怎么回」在那两种轮次上是假的，它们既不是私聊也不是
   团队房间、也没有那行开头。改成有条件的真话：「当其中一种唤醒了你时，开头会说是哪种」。
   spec §6.1/P7 想要的那个由 registry 统一渲染的来源声明仍未建（见 §13 待补条目），这句话
   在它建成前不能替它做承诺。
2. 块教两个发送动词，而 `disallowed_tools` 每轮撤掉其中一个的 schema —— 于是「提示点名
   一个不存在的工具」这个本改造要消灭的失败，出现在本改造自己的介绍里。字节稳定排除了分支，
   唯一可用的修法是说真话：明写每轮只有一个、并给出想联系另一种处境时的下一步（结束本轮）。

原守卫 `test_the_delivery_rule_names_no_surface_specific_tool` 的 docstring 声称「块里不得
出现任何 per-surface 工具」，实际只检查一行 —— 声明已收窄到它真正守的东西（通用投递规则那
一行不得绑定某个面的动词），并新增一条钉住「块必须承认只有一个动词在桌上」。

## 2026-08-18 (二) — `_room_labels` 打的是裸 backend，SQLite 上一直静默返回空

两条查询用 `%s` 打 `bus._db`。`LocalMessageBus` 原样保存传入的 **raw backend**，而 SQLite 裸
backend 把 SQL 直接交给 aiosqlite —— `%s` 在那里不是占位符。于是两条都抛异常、fail-open 吞掉、
SQLite 上这张映射**永远是空的**：每一条团队房间消息都渲染成私聊短形式，正是这个函数自己
docstring 里写「比缺标签更糟」的那种误标。MySQL 上正常，所以这是一处铁律 #7 的裂口，且桌面端
在坏的那一侧，日志里 debug 以上什么都没有。

`team_posting.team_cascade_depth` 带着专门讲这个陷阱的注释块，写在本函数之前两个 commit，
没能阻止它。所以现在写成直白一句：**`%s` 查询属于 client，而 `bus._db` 不是 client。**

6433 个通过的测试没碰到它 —— `test_turn_context_split.py` 用手写的 `bus_room_labels` 覆盖
渲染器，自带输入的渲染器测试证明渲染器，对输入从哪来一言不发。新增
`tests/message_bus/test_room_labels_producer.py` 打真实数据库，已变异验证。

## 2026-09-04 · no per-module port (batch 5a)

The MCP server URL comes from `mcp_server_url("<server_name>")` (the single MCP host + `/mcp/<server_name>/sse`); the module-level port constant / `self.port` and the factory's `port` parameter are gone.

## 2026-09-04 · declares its platform metadata (batch 5b)

`get_config()` is static and carries what the platform used to table about this module (default / base / always-load membership, instance prefix, role, display, decision metadata as applicable).

## 2026-09-04 · declares its agent-level instance (batch 5b.2)

`agent_instance` in `get_config()` (description / keywords / topic hint) replaces the factory's or descriptor's copy; the InstanceFactory creates it for every agent.

## 2026-09-04 · declares its discovery / long-run flag (batch 5d)

`discovery_hidden` (awareness / basic info / message bus) or `long_running_instances` (skills) moved from platform lists into `get_config()`.
