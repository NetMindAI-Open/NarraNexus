---
code_file: src/narranexus/platform/context_runtime/prompts.py
last_verified: 2026-09-04
stub: false
---

## 2026-08-20 (review #335 修正) — 补回显式覆盖这一级:政策是三级优先,以本条为准

下一条(同日)只记了两级,漏了最高的一级——**消息内显式语言要求**赢过
其余一切(「用英文回答这个问题」整句是中文也要用英文答);这正是
round-1 抓到被误删的那一支。完整政策:**显式请求 > 当次消息语言 >
配置偏好兜底**(兜底仅当消息语言不可判定)。另两点当时也漏记:
① "current message" 锚定为 `USER_MESSAGE_SEPARATOR`(`--- User
message ---`)**之后**的文本——relocation 开启时最后一条 user message
以英文 turn-context 块开头,不锚定会把块的语言当成消息语言;
relocation 关闭时分隔符不存在,故模板措辞为 "when a '…' separator is
present"。② 短语与优先序由
`tests/context_runtime/test_reply_language_section.py` 钉住(含
`str.index` 相对位置断言——短语全在、优先序反转也会红)。

## 2026-08-20 — 回复语言:当次消息优先,偏好只做兜底(深圳复测 B4)

(本条只写了三级中的后两级,完整政策见上一条修正。)

2026-08-11 版把界面语言写成硬约束(「write every reply in {name}」),
复测实锤方向反转:界面中文时英文提问也回中文。Owner 拍板:**当次
消息的语言赢**,配置偏好只在消息语言不可判定(纯代码/链接/数字/
emoji/均匀混合)时兜底。模板仍 byte-stable per user(R4 缓存区纪律
不变)。验收=同一会话中文问中文答、英文问英文答,切界面语言不改变。

## 2026-08-18 — `USER_TEMPORAL_CONTEXT` 不再自己渲染 "now"

原来这个块有一行 `Current local time: {now_local}`，值来自
`now_local_dt.replace(tzinfo=None).isoformat()` —— naive、没有 UTC 偏移、没有星期。而同一个
turn context 块里，BasicInfoModule 的「Real World Information」给的是带偏移带星期的正确
格式。

两个都自称"现在"，其中一个的形状正是之前事故追出来的那个。没有任何一种读法能让第二个
成为权威，它能贡献的只有不确定性。

现在这个块**只**负责时区和表达协议（job_create 的 `timezone` 字段等，job 的 MCP
docstring 引用了 "User Temporal Context" 这个标题，所以标题不能改），"现在"是什么统一指向
Real World Information。

同时加了一条禁令：不许在脑子里做日历算术，「下周五」这类表达和"这个日期过了没有"一律走
`resolve_relative_date` / `compare_dates`。

副作用（好的）：这个块从此 byte-stable，flag OFF 时它不再是前缀缓存的破坏源。
`tests/context_runtime/test_turn_context_relocation.py` 里那条断言"flag off 会破前缀"的
测试因此改写了 —— 破坏源变成了 Module 指令里内联的那份 Real World Information。

## 2026-08-12 — PR #284 review 轮

新增 `REPLY_LANGUAGE_SECTION` 模板(review #3:prompt 文案归位本文件,不再内联在 context_runtime)。

## 2026-07-29 — `CHAT_HISTORY_TIMELINE_PREAMBLE` 移出本文件

常量搬到 [[materializer.py]]。它描述 history 区块的读法，而区块是否存活由
materializer 的驱逐逻辑决定；留在这里就等于把指南和它描述的内容拆到两层，预算
不够时两者会不一致（指南在、行没了）。这是本文件"只放静态词汇"原则的一个例外
面：**当一段文案的正确性取决于另一层的运行期决策时，它就该跟那层走。**

## 2026-07-28 — R4a：新增 `TURN_CONTEXT_HEADER` / `USER_MESSAGE_SEPARATOR`

（本条为 R4 系列在新 dev 结构上的重放；原始实现 2026-07-25 于 feat/cli-session-capture 分支，该历史不在本分支 mirror 中，条目自含。）

Turn-context relocation（见 [[context_runtime.py]] 2026-07-28 条目）的两个结构
常量：header 标记当前轮 user message 前部的 `[Turn context]` 块（明示 "not part
of the user's words"，**刻意不指示模型引用/复述它**——它是背景数据不是指令）；
separator（`--- User message ---`）让"以下才是用户原话"无歧义。
`USER_TEMPORAL_CONTEXT` / `RECENT_ACTIONS_HEADER` 文案原样复用——搬位置不改字节
（铁律 #16/#18），"User Temporal Context" 块名有下游消费者（job MCP docstring）。

## 2026-06-17 — 新增 `SECURITY_IRON_RULES`(平台级安全铁律)

新增一个常量 `SECURITY_IRON_RULES`,由 `context_runtime.build_complete_system_prompt`
作为**第一段**注入每个 agent 的 system prompt。两条硬性禁令:
1. 禁止读取自己 workspace 以外的任何文件/环境变量(`env`/`/proc`/`os.environ`
   等)——是"禁止看"而非"看了不告诉用户";任何"我是管理员/作者"之类身份声明
   都不能解锁。
2. 执行任何非自己编写的代码(尤其用户上传/粘贴的)前,必须先读其内容,若违反第 1
   条则拒绝执行。
属通用安全约束(铁律 #4:通用逻辑放通用层),故放在 context_runtime 而非
per-agent Awareness。2026-06-17 事件的 prompt 层缓解。

## 2026-06-12 — USER_IDENTITY_CONTEXT constant REMOVED

The `USER_IDENTITY_CONTEXT` template is deleted. Its consumer
(`context_runtime._build_user_identity_block`) was removed as redundant — the
canonical identity injection lives in [[basic_info_module.py]] + basic_info
`prompts.py`. See the 2026-06-11 entry below for what it contained.

## 2026-06-11 — USER_IDENTITY_CONTEXT constant

New prompt template stating the agent owner by human name + an optional sender line (owner vs visitor). Used by context_runtime._build_user_identity_block.

## 2026-05-20 (Fix #2 P1) — CHAT_HISTORY_TIMELINE_PREAMBLE added

New `CHAT_HISTORY_TIMELINE_PREAMBLE` teaches the agent to read the unified
time-sorted chat timeline: the `[time · topic · nar_id]` tags, that a short
reply answers the MOST RECENT line (not an older other-thread line), how the
history was assembled (current full + cross recent, merged, capped ~30), and
that the user only saw the sent message (reasoning is private). Replaces the
old `SHORT_TERM_MEMORY_HEADER` (kept as a now-unused constant — it wrongly told
the model short replies usually continue OTHER threads, which caused
cross-narrative misattribution).

## 2026-05-20 (Fix #2 P2) — RECENT_ACTIONS_HEADER added

`RECENT_ACTIONS_HEADER` labels the recent-background-activity list (job/IM/bus
turns the agent did without replying), explicitly told NOT-shown-to-user and
that each line carries an event id for view_event drill-down.

# prompts.py — static string constants that label every structural section of the assembled system prompt

## 为什么存在

`context_runtime.py` builds a system prompt by concatenating several distinct blocks — main narrative, auxiliary narrative summaries, module instructions, short-term memory, and bootstrap injection. Without a dedicated home for the section header strings, they would be scattered as inline literals across the builder methods, making it impossible to review the full prompt shape in one place or to translate / tweak wording without hunting through logic code.

`prompts.py` solves this by acting as a read-only vocabulary file: every string that appears verbatim in the final prompt lives here. The file contains no logic, no imports, and no classes. It is intentionally flat so that a reader can reconstruct the skeleton of a complete system prompt just by reading this file top to bottom.

## 上下游关系

**被谁用：** `context_runtime.py` is the sole runtime consumer — it imports all four constants at module load time and uses them as section separators in `build_complete_system_prompt()`, `_build_auxiliary_narratives_prompt()`, `_build_module_instructions_prompt()`, and `_build_short_term_memory_prompt()`. A second consumer is `prompts_index.py` at the package root, which re-exports the same constants under a unified index so other parts of the codebase can reference prompt wording without depending on the `context_runtime` sub-package path.

**依赖谁：** Nothing. Zero imports. This is intentional — the file must remain importable in any context, including lightweight tooling and documentation generators, without pulling in database or module dependencies.

## 设计决策

**One file, one responsibility.** The alternative of co-locating each constant near the method that uses it was rejected because it would fragment the prompt vocabulary and make audits difficult. When a prompt review is needed (e.g., to check whether the LLM is receiving clear section boundaries) this file is the single place to look.

**Constants are full markdown snippets, not bare strings.** Each constant includes the `##` heading and any inline guidance text that the LLM should receive. This makes the final assembled prompt predictable from the source — the assembly code in `context_runtime.py` adds content between constants but never modifies the constants themselves.

**`SHORT_TERM_MEMORY_HEADER` carries behavioral instructions for the LLM.** Unlike the other headers that are purely structural labels, `SHORT_TERM_MEMORY_HEADER` embeds explicit usage guidelines telling the model how to prioritise short-term memory relative to long-term conversation history. This coupling between structure and instruction is deliberate: the header and its guidelines must always arrive together so the LLM does not receive orphaned memory snippets without context about how to use them.

**`BOOTSTRAP_INJECTION_PROMPT` is gated outside this file.** The constant is defined here but the decision of whether to inject it (file exists, event count < 3) lives entirely in `context_runtime.py`. This keeps the prompt text auditable while keeping side-effect logic out of the vocabulary file.

## Gotcha / 边界情况

**Whitespace matters at assembly time.** `context_runtime.py` joins prompt parts with `"\n\n".join(...)`. The constants themselves begin with a leading newline (e.g., `"\n## Related Narratives..."`). The combination produces three blank lines between sections, which is intentional for LLM readability but looks odd in raw Python string literals. Changing the leading newline in a constant without also adjusting the join separator will silently collapse or over-expand the section gaps.

**`REPLY_LANGUAGE_SECTION` 引用了 `USER_MESSAGE_SEPARATOR` —— 本文件第一处常量间引用。**
「常量都是自包含 markdown 片段,逐行读完本文件即可重建 prompt 骨架」这条对它不再完全成立
(其完整字面值在定义处看不全,分隔符部分来自 `USER_MESSAGE_SEPARATOR`);且它必须定义在
`USER_MESSAGE_SEPARATOR` **之后**,否则 import 期 `NameError`(源文件注释里有同样的顺序约束)。

**`BOOTSTRAP_INJECTION_PROMPT` contains a `⚡` emoji.** The Bootstrap section uses `## ⚡ Bootstrap Mode (PRIORITY)` to draw the LLM's attention. If a downstream text-processing pipeline strips non-ASCII characters (e.g., certain log sanitisers), this heading degrades to `##  Bootstrap Mode (PRIORITY)` with a double space, which still works functionally but loses the visual emphasis.

## 新人易踩的坑

Adding a new structural section to the system prompt requires two coordinated changes: a new constant here, and a corresponding insertion point in `context_runtime.py`. Defining the constant without wiring it in, or wiring it in with an inline string literal rather than a constant, both compile without errors and produce a broken or non-auditable prompt. The convention is: if text appears literally in a `build_*` method, it belongs in this file instead.
