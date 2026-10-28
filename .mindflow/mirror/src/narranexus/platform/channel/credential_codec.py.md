---
code_file: src/narranexus/platform/channel/credential_codec.py
last_verified: 2026-09-04
stub: false
---

# channel/credential_codec.py — secret half of a generic credential

## Intent

Encrypts/decrypts `channel_credentials.secret_json` with the marketplace's Fernet `SecretBox` (env key on cloud, per-install key file locally) — real encryption at rest instead of the per-channel base64 the old tables used. Lazy singleton; `use_key_dir` points tests at a temp key.
