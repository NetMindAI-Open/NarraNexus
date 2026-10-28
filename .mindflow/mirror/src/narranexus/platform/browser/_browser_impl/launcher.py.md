---
code_file: src/narranexus/platform/browser/_browser_impl/launcher.py
last_verified: 2026-09-22
stub: false
---

# launcher.py — Chromium 启动参数与 CDP 端点发现

## 为什么存在 / 关键决定

argv 是安全面，不是格式细节：

- 调试端口**只绑 loopback**。不绑等于给同网段任何人一个在用户机器上的远程执行；
- **web security 保持开启**。关掉等于把继承来的登录态，交给 agent 访问到的任何页面；
- profile 目录显式且按身份隔离，登录态跨会话存活，两个身份不共用 cookie jar。

**默认有头。** 这是 ego-lite 那一课直接付的学费：截屏流需要合成器，不绘制的浏览器
只发一帧然后没了——而那个故障与「页面没变化」无法区分。

端点发现要重试：端口在浏览器能应答**之前**就已经接受连接，单次尝试会在慢机器上
flaky 成一个空白面板。超时错误带上**最后一次探测的原因**——光说「超时」对读日志的人毫无信息。

profile 名不合法时是**替换**而不是净化：把 `../../etc` 悄悄改写成形似的东西，
会让两个不同的配置 profile 无声地撞在一起。

## 上下游

- 设计意图：本目录各 mirror md（`.mindflow/mirror/src/narranexus/platform/browser/`）
