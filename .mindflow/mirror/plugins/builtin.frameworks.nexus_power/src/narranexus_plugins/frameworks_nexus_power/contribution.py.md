---
code_file: plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/contribution.py
last_verified: 2026-09-07
stub: false
---

# builtin.frameworks.nexus_power — contribution.py

`CONTRIBUTION` for the default framework `nexus_power`: a factory that builds the NexusPower adapter (no external SDK, so `FrameworkInstall` declares nothing to install). Named by the builtin manifest and registered lazily by `loop.driver.ensure_builtin_frameworks()` (batch 6b.2b).

## 2026-09-07 — META 携带框架事实（B6）

FrameworkMeta 增补 protocol/oauth_source/runtime_name/login_marker（值即原宿主七张名字表里属于本框架的那一行），导出为 META；宿主全部在调用期从注册表派生，本插件是这些事实的唯一持有者。

## 2026-09-07（round-2 P2-I3 / A2-5）— META declares the capabilities and the cloud-safety fact

`capabilities = {"event_log", "steering", "native_replay"}` (the static twin of
`NexusAgent.capabilities()`, read by the remote executor shell and by history projection before any
driver exists) and `uses_shared_cli_login=False` — it drives the provider API with the key of the
card bound to the agent slot and refuses subscription OAuth outright, which is WHY the cloud gate
lets a non-staff user select it. Both facts used to be platform-side frozensets keyed on this
framework's name.
