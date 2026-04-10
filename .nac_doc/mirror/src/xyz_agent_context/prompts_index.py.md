# prompts_index.py

Centralized index of all prompt constants in the project — one import to see every prompt, with IDE-navigable references to their source files.

## Why it exists

Prompt strings are spread across multiple `prompts.py` files in different submodules (context_runtime, narrative, module). When a developer needs to audit or tune the prompt set — to check for contradictory instructions, to understand token budget, or to trace where a specific instruction comes from — they would have to know which submodule's `prompts.py` to look in. `prompts_index.py` aggregates all prompt constants in one place with inline comments mapping each constant to its source file. It does not own any prompts; it only re-exports them.

## Upstream / Downstream

**Re-exports from:** `context_runtime/prompts.py`, `narrative/_narrative_impl/prompts.py`, `narrative/_event_impl/prompts.py`, `module/_module_impl/prompts.py`, and `agent_runtime/prompts.py`.

**Consumed by:** developers and tooling that want a single entry point for prompt inspection. Production code should import prompts from their source module (`context_runtime.prompts`) rather than through this index, to avoid loading the entire prompt set when only one prompt is needed.

## Design decisions

**Re-export only, no modification.** This file contains no logic. Every constant imported here is the exact constant from its source file. If a constant is changed in the source, the change is automatically reflected here.

**Grouped by subsystem with source file comments.** The five import blocks correspond to the five subsystems that define prompts. Each block has a header comment with the source file path so developers can jump directly to the definition.

**Not included in `__init__.py` exports.** `prompts_index.py` is not re-exported from the package root. It is a developer tool, not part of the runtime public API.

## Gotchas

**Importing this file loads all five prompt modules.** Each block does a from-import that executes the source `prompts.py`. For test environments or tooling that only needs one prompt module, this is unnecessary overhead. Import directly from the source module.

**New-contributor trap.** When adding a new prompt constant to any `prompts.py`, also add it to the corresponding block in `prompts_index.py` with a comment. A prompt that is missing from the index will not appear in the consolidated view, defeating the purpose of the file.
