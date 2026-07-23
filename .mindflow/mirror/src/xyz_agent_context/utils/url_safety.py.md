---
code_file: src/xyz_agent_context/utils/url_safety.py
last_verified: 2026-07-22
stub: false
---

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
