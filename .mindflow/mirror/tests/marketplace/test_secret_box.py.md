---
code_file: tests/marketplace/test_secret_box.py
last_verified: 2026-08-13
stub: false
---

## 2026-08-13 — fail-closed 契约用例 + 三元组

新增/更新:token 形状解不开必须**抛 SecretDecryptError**(不再 garbage passthrough);`decrypt_env_config` 返回 3 元组 `(plain, needs_rewrite, failed_keys)`,坏 key 排除出 plain。旧「garbage passthrough」语义仅对 genuinely-plain 非 token 值成立。

# test_secret_box.py

Unit tests for `SecretBox`: roundtrip, legacy-base64 fallback + the
`needs_rewrite` lazy-migration flag, garbage passthrough (never destroy
uninterpretable values), key-file 0600 creation and reuse, env-var key
precedence, and fail-fast on an invalid `SKILL_SECRETS_KEY`. Pure-filesystem
tests (tmp_path); no DB fixture needed.
