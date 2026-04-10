---
code_file: src/xyz_agent_context/context_runtime/prompts.py
last_verified: 2026-04-10
stub: false
---

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
