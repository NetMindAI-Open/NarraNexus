---
code_file: tests/marketplace/test_install_pipeline.py
last_verified: 2026-07-20
stub: false
---

# test_install_pipeline.py

End-to-end pipeline tests on a real (tmp) filesystem + in-memory DB.
The `workspace` fixture isolates `settings.base_working_path`, resets the
SecretBox singleton, and replaces `backup_after_api_install` with a recorder
(the real one writes under the developer's home directory).

Key scenarios: happy zip install asserts every meta field the pipeline adds
(hash/content_hash/updated_at/version) plus the audit row and backup call;
the malicious-zip test proves the gate fires BEFORE `skills/` is touched
(no dir, no audit row, no backup); replace proves env_config survives an
upgrade in decryptable form; github flow is tested by stubbing
`fetch_github_repo` (no network in tests); the two SecretBox tests pin the
storage format (Fernet `gAAAA` prefix, never plaintext/base64) and the lazy
migration rewrite of legacy base64 values.
