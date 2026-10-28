---
code_file: packages/narranexus-contracts/src/narranexus/contracts/prompt.py
last_verified: 2026-09-07
stub: false
---

# contracts/prompt.py — the prompt contracts

Kind `prompt`: `PromptContext` (what a section sees: agent/user, ctx_data, narratives, module instructions, db, the host runtime, deployment mode, mutable `part_sizes`/`meta`), `PromptSectionProvider` (slot `prompt.sections`, many: `id`, `order`, `budget_chars`, `render(ctx)`), `PromptAssembler` (slot `prompt.assembler`, one: `assemble(sections, ctx)`), `RenderedSection`, `budget_report()`.

## 2026-09-07 — RenderedSection.budget_chars; budget_report defaults to it

A rendered section carries its provider's declared budget; budget_report(sections, budgets=None) uses it unless a budgets mapping overrides by id. Before this the only assembler was constructed without budgets and the overrun warning was a dead branch.

## 2026-09-07（round-2 P2-I4）— `required_in`: a section may declare itself load-bearing per deployment mode

`PromptSectionProvider.required_in: tuple[str, ...]` names the deployment modes in which an empty or
raising render refuses the whole prompt (`RequiredSectionMissing`) instead of quietly shipping a turn
without the section. Empty (the default) keeps the degradable behaviour every plugin section wants.

Per MODE rather than a plain `required: bool` because the section this exists for —
`builtin.prompts.SecuritySection` — legitimately renders nothing on a desktop build; an unconditional
flag would fail every local turn. `section_required()` reads the field with `getattr` so a section
written against the older Protocol is treated as degradable rather than crashing the host's loop.
