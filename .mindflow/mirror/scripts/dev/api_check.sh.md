---
code_file: scripts/dev/api_check.sh
last_verified: 2026-09-04
stub: false
---

# scripts/dev/api_check.sh — public API check

`griffe check` of `narranexus.contracts` and `narranexus.sdk` against a base git ref (default `origin/main`), searching both the packages/ layout and the pre-6d `src/` layout so the comparison works across the move; exits non-zero on a breaking change. Run by CI on every PR.
