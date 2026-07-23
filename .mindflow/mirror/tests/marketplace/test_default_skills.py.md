---
code_file: tests/marketplace/test_default_skills.py
last_verified: 2026-07-21
stub: false
---

# test_default_skills.py

Stage-9 coverage: manifest default flag → catalog; list_defaults
latest-only; install_defaults install/skip/unreachable-degrade (the
unreachable test must neutralize BOTH the env var and the settings field
for the local-registry override — the developer's .env sets it); NetMind
key runtime injection (provider row → injected; explicit config wins; no
provider → absent; never persisted to .skill_meta.json), and
env_configured treating platform vars as satisfied.
