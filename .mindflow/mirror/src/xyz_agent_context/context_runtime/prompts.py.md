---
code_file: src/xyz_agent_context/context_runtime/prompts.py
last_verified: 2026-07-29
stub: false
---

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

**`BOOTSTRAP_INJECTION_PROMPT` contains a `⚡` emoji.** The Bootstrap section uses `## ⚡ Bootstrap Mode (PRIORITY)` to draw the LLM's attention. If a downstream text-processing pipeline strips non-ASCII characters (e.g., certain log sanitisers), this heading degrades to `##  Bootstrap Mode (PRIORITY)` with a double space, which still works functionally but loses the visual emphasis.

## 新人易踩的坑

Adding a new structural section to the system prompt requires two coordinated changes: a new constant here, and a corresponding insertion point in `context_runtime.py`. Defining the constant without wiring it in, or wiring it in with an inline string literal rather than a constant, both compile without errors and produce a broken or non-auditable prompt. The convention is: if text appears literally in a `build_*` method, it belongs in this file instead.
