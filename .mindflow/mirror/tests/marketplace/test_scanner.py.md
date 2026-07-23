---
code_file: tests/marketplace/test_scanner.py
last_verified: 2026-07-20
stub: false
---

# test_scanner.py

Rule-by-rule scanner tests on tmp_path skill fixtures: every LOW rule via a
parametrized malicious snippet, both HIGH rules in multiple shapes, status
aggregation (rejected/warning/passed), and the hardening cases — unparsable
Python is flagged AND still regex-scanned (syntax-error bypass attempt),
binary skip, issue file/line accuracy, dependency-audit hit/clean/disabled,
plus the false-positive regression: bare word "credentials" in docs must
stay `passed` while `~/.aws/credentials` rejects.
