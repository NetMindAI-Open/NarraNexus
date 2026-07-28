---
code_file: src/xyz_agent_context/marketplace/_skill_marketplace_impl/scanner/patterns.py
last_verified: 2026-07-20
stub: false
---

# scanner/patterns.py

Data-only rule tables, kept separate from the engine so rule review/bump is a
one-file diff (and `SCANNER_VERSION` lives here — bump it when rules change so
`skill_scan_results` rows are attributable to a rule set).

## Rule inventory (per Phase 4 §7.4, adjusted)

- **HIGH → REJECT (text regex, all scannable files)**: `shell_pipe_exec`
  (curl/wget piped into a shell), `sensitive_path` (~/.ssh, /etc/passwd,
  /etc/shadow, ~/.aws, .env, path-like credentials).
- **LOW → WARN (AST call sites)**: eval_exec, compile_call, dynamic_import,
  subprocess_exec (os.system/os.popen/subprocess.*), network_post
  (requests/httpx post/put), socket_usage, fs_walk (os.walk; glob.glob only
  with a `**` argument), pickle_load, base64_decode, symlink.
- **LOW extras beyond the original 12**: `unparsable_python` (a syntax error
  must be visible, not a silent AST bypass) and `vulnerable_dependency`
  (audit.py).

## Deliberate choices

- **`credentials` matches only in path-like form** (`.aws/credentials`,
  `.git-credentials`, `dir/credentials`) — the bare English word appears in
  legitimate skill docs ("register your credentials…") and must not reject.
  Regression-tested.
- `.env` DOES match in prose (spec-mandated HIGH); known false-positive
  source, revisit if it bites real skills.
- `KNOWN_VULNERABLE` is a static MVP advisory list; upgrading to a live
  Safety/OSV feed touches only this dict + audit.py.
- Module-prefix matching (`"subprocess."`) flags any attribute call of that
  module — coarse on purpose; LOW severity tolerates false positives.
