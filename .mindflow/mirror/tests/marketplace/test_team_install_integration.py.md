---
code_file: tests/marketplace/test_team_install_integration.py
last_verified: 2026-07-21
stub: false
---

# test_team_install_integration.py

End-to-end install against the REAL bundle importer using a real pm-bridge-bot.nxbundle fixture: publish → install_preflight → assert token + manifest agents. Skipped if the fixture blob is absent.
