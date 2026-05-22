---
code_file: real_case_e2e_test/core/semantic.py
last_verified: 2026-05-13
stub: false
---

# semantic.py — shell out to claude CLI for a per-case verdict

## Why it exists

Some questions only make sense to a reader: did the agent answer the
user, or did it drift to meta? Did the second turn maintain context
from the first? The harness can't see this from timing + tool counts.
We pipe everything we already captured into the local Claude Code CLI
with a fixed prompt and embed the markdown verdict next to the
transcript.

## Decisions

- We invoke `claude -p --output-format text` via subprocess and stream
  the prompt through stdin. JSON output would be nicer to parse but
  the verdict is shown to humans, so text + downstream embed wins.
- Best-effort by design: missing CLI / non-zero exit / timeout all
  produce a `SemanticResult` with the failure noted, but never abort
  the suite. The programmatic gate is the hard verdict.
- The prompt template lives at `prompts/semantic_per_case.md` so a
  prompt change is reviewable as a diff in its own file; the code
  here is plumbing.
