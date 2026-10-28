---
code_file: src/narranexus/kernel/plugins/install/installer.py
last_verified: 2026-09-07
stub: false
---

## 2026-09-04（批 3b）— manifest 校验改用 `slot_tree_with_builtins()`（含内置声明的位）

## 2026-09-03（批 2c）— 安装流水线

fetch 到 plugin home 下的 staging → manifest 按本宿主校验（不满足时若有 `versions.json` 提示该装哪个 tag）→
黑名单 → 已装且非 replace 拒绝 → eager 依赖装进 staging/pyenv → copy 模式整目录搬到 `plugin_dir(id)`
（旧版本先改名再删）、link 模式原地 → 最后才写 registry（写前 LKG 快照）。任一步失败：staging 清理、registry
不动（测试钉住「依赖失败不落账」）。`uninstall` 只删 plugin home 之内的 copy 目录与 link 模式的私有依赖；
`check_update` 查 GitHub latest release；`upgrade` 按原来源重装（replace）。

## 2026-09-07 — permissions are a gate; acknowledgement records tokens; upgrades that widen re-arm

A plugin that declares any permission is installed enabled=False (state registered) unless permissions_acknowledged — before this the 'acknowledge with …' hint was decoration and the plugin ran on the next boot. acknowledge_permissions(store, id) is the one door (CLI --ack, factory endpoint): it records the acknowledged permission tokens on the record and enables. upgrade() compares the new manifest's tokens with the acknowledged set and re-arms the gate when they grew. is_gated(rec) is what enable paths consult.

## 2026-09-07 — blocked=None refuses remote installs

When the blocklist is unknown (None) the installer refuses network sources fail-closed and still installs local paths (an offline desktop must develop plugins).

## 2026-09-07 — check_updates closes its client

Through sources._http.

## 2026-09-07 — slug 唯一（round-2 K2-I3）

acme.auth-sso 与 acme.auth_sso 共享 ext_/NXP_/nxplugins 前缀：安装时若 slug 已被已装插件或 builtin 持有则 InstallError。
