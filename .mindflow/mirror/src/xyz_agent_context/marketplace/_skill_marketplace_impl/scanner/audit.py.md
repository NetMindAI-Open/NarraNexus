---
code_file: src/xyz_agent_context/marketplace/_skill_marketplace_impl/scanner/audit.py
last_verified: 2026-07-20
stub: false
---

# scanner/audit.py

Dependency audit: parses a skill's `requirements.txt` and flags exact pins
matching `patterns.KNOWN_VULNERABLE` (LOW / `vulnerable_dependency`).

## Scope decisions (MVP)

- Only `==X.Y.Z` pins are checked — range specifiers would need a real
  resolver to know which version installs; unresolvable lines are ignored
  rather than guessed at.
- Advisory data is the static in-repo dict; swapping in a live Safety/OSV
  feed changes only this file + that dict.
- No network calls ever — must run on desktop installs offline.
