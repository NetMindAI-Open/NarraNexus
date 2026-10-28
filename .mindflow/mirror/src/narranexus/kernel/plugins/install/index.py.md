---
code_file: src/narranexus/kernel/plugins/install/index.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-03（批 2c）— 官方索引与黑名单（本地缓存一天）

`index.json` 只有元数据（进索引是元数据检查不是代码审查，UI 如实说）；`blocked_versions.json` 启动与安装前
都查。按 ttl 刷新，网络失败用缓存——没网不能让工场页打不开。

## 2026-09-07 — blocklist state is honest; ids validated; bounded fetch

blocked() raises IndexUnavailable when the blocklist was never obtained and nothing is cached (a stale cache is still an answer); cached_blocked() reads the cache without network for boot. Index entries must satisfy the manifest PLUGIN_ID_RE and never start with builtin. (the publish-time validator's rules, enforced at read time). A fetched file is capped at MAX_INDEX_BYTES.

## 2026-09-07 — self-created client closed

The index fetch uses sources._http so a client it creates is closed.
