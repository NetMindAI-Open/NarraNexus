---
code_file: src/narranexus/kernel/plugins/install/integrity.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2c）— sha256 与 SRI

记进 registry 的资产哈希、升级时对比用的校验、前端 bundle 的 `sha256-<base64>` SRI 值。

## 2026-09-07 — scope stated: no source verification

The header now says what the hashes are for (local tamper detection after install) and what does not exist (publisher signing / pinned digests); the never-called verify_sha256 is gone rather than suggesting a verification that nothing performed.
