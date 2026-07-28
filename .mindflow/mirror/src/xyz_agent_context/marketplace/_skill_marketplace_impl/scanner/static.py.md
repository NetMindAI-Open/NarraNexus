---
code_file: src/xyz_agent_context/marketplace/_skill_marketplace_impl/scanner/static.py
last_verified: 2026-07-20
stub: false
---

# scanner/static.py

The scan engine: walks a skill dir, regex-scans text files (HIGH rules),
AST-scans `.py` files (LOW rules), folds in the dependency audit, aggregates
a verdict (`any HIGH → rejected; any LOW → warning; else passed`).

## Design decisions

- **Syntax errors can't dodge the gate**: unparsable Python is itself a LOW
  finding AND the file still went through the regex pass (text rules run
  before AST), so a deliberately broken .py with `curl|bash` inside still
  REJECTS. Tested explicitly.
- **Binary/oversized files are skipped** (NUL-byte sniff + 1MB cap +
  suffix allowlist). A zip full of binaries can't stall publish; actual
  binary payload risk is the runtime sandbox's dept, not the static gate's.
- `.skill_meta.json` is excluded — it contains encrypted env values and
  install bookkeeping, not skill-author content.
- `_dotted_name` resolves only literal `a.b.c` attribute chains; aliased or
  computed calls escape AST rules. Documented limitation — obfuscation
  resistance is what the runtime isolation layer is for (spec §7).
- Issues carry (rule, severity, relative file, line, detail) and serialize
  via `to_dict()` straight into `skill_scan_results.issues_json`.

## Upstream / Downstream

Called by: publish pipeline (stage ④ registry) and InstallPipeline step 4.5
(URL/GitHub sources re-scan; marketplace sources are hash-verified instead).
Consumes: patterns.py tables, audit.py findings.
