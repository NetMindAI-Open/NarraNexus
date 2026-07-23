---
code_file: tests/marketplace/test_secret_box.py
last_verified: 2026-07-20
stub: false
---

# test_secret_box.py

Unit tests for `SecretBox`: roundtrip, legacy-base64 fallback + the
`needs_rewrite` lazy-migration flag, garbage passthrough (never destroy
uninterpretable values), key-file 0600 creation and reuse, env-var key
precedence, and fail-fast on an invalid `SKILL_SECRETS_KEY`. Pure-filesystem
tests (tmp_path); no DB fixture needed.
