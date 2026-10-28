---
code_file: src/narranexus/platform/prompt_slots.py
last_verified: 2026-09-07
stub: false
---

# platform/prompt_slots.py — the prompt slot seam

`ensure_registered` (lazy manifest registration of builtin.prompts), `sections_for(registries)` (bound order when bound, else declared `order`), `assembler_for(registries)` (the bound `prompt.assembler`). ContextRuntime builds the system prompt only through these.

## 2026-09-07 — no ensure_registered

sections_for / assembler_for read the booted registries through kernel.plugins.bound; an empty slot answers empty, never a lazily re-registered builtin.

## 2026-09-07（round-2 P2-I4）— an empty section slot is now loud, and required sections are enforced downstream

`sections_for` still answers `[]` for an empty slot (never a silently re-registered one), but logs at
ERROR: an unbooted process would otherwise grow a blank system prompt with no trace. The
load-bearing/degradable distinction lives on the provider (`PromptSectionProvider.required_in`) and
is enforced by the render loop in `context_runtime`, which refuses to produce a prompt when a section
required in the current deployment mode renders empty or raises. Residual gap, stated on purpose: a
distribution that removes the PROVIDER entirely cannot be caught here (there is nothing left to ask),
which is why the empty slot is logged rather than ignored.
