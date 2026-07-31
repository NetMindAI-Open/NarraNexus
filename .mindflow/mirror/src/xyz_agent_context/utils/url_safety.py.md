---
code_file: src/xyz_agent_context/utils/url_safety.py
last_verified: 2026-07-30
stub: false
---

## 2026-07-30 — 新增 `is_obviously_non_public_host`（同步、不做 DNS）

第二个函数，回答的是**另一个问题**。`assert_public_http_url` 守的是「服务端自己去
抓一个别人给的 URL」（SSRF，安全边界）；这个守的是「把一个**配置里的** origin 交给
第三方 API 之前，它明显不是公网地址吗」。

首个调用方：[[billing]] 的 `_return_urls`。NetMind 边缘对 loopback/私网 host 的请求
回 HTML 403（任何 scheme），而我们的 client 把 403 映射成 auth 错、路由报 401
「token 无效」—— 所以发出去等于毁掉用户付款还甩锅给他的登录状态。

**刻意不做 DNS**：调用方在付款路由里同步决定发不发，一次 DNS 抖动不该改变这个决定。
因此返回 False 的含义是「不是明显的私网」，**不是**「已确认公网」——这条差别写进了
docstring，免得有人把它当 SSRF 门用。IP 段判定复用既有的 `_is_public_ip`，
知识仍然只有一份。

覆盖的是运维真会手写的那几类：字面私网/回环/link-local IP、`localhost`、
`*.localhost`、`.local`（mDNS）、以及 `my-nas` 这种单标签名（公网永远解析不出来）。

# url_safety.py — SSRF gate for server-side outbound HTTP

## Why it exists

Whenever the SERVER fetches a user/agent-supplied URL it can be tricked into
hitting internal services (SSRF), and on EC2 the metadata endpoint
`169.254.169.254`. `assert_public_http_url` is the single gate every such
fetch must pass. First consumer: the URL-tab embed probe ([[embed_probe.py]]).
Deliberately in `utils/` (not the artifact package) because the planned
headless RenderService and streaming browser (方案三) will reuse it — SSRF is
written once.

## Contract

- Rejects non-http(s) schemes, no-host URLs.
- Literal-IP hosts validated directly (no DNS); hostnames resolved via an
  injectable `Resolver` (real DNS by default, mockable in tests).
- Rejects if ANY resolved address is private / loopback / link-local
  (covers the metadata IP) / reserved / multicast / unspecified — IPv4 and
  IPv6. Validation is POST-resolution, which is what defeats DNS-rebinding.
- Resolution failure / empty resolution is a HARD reject, never a pass.

## Trust boundary gotcha

This guards requests WE originate. An `<iframe src>` is fetched by the
USER's browser, not us, so it is not on this SSRF surface — but open_url
still validates the initial URL here to refuse obviously-internal targets
early. Do not conflate the two.
