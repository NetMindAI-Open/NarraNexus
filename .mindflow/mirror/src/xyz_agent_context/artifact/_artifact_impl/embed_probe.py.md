---
code_file: src/xyz_agent_context/artifact/_artifact_impl/embed_probe.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — redirect walk extracted to safe_http

The manual redirect-following + per-hop SSRF loop moved to the shared
[[safe_http.py]] `safe_stream_get`. `probe_url` now just opens that context and
classifies the final hop's headers (`str(resp.url)` is the final URL). The
duplicate copy in [[page_text.py]] is gone — the SSRF walk has one home.

# embed_probe.py — can this URL be iframe-embedded, or must it stream?

## Why it exists

A URL tab is shown either inline (iframe) or via a fallback/stream card,
decided by the target's own anti-embedding headers (X-Frame-Options / CSP
frame-ancestors), which we cannot override. This module makes that decision.

## Two parts, split for testability

- **`classify_embeddability(...)`** — PURE function: response headers + our
  serving scheme in, `EmbedVerdict` out. Exhaustively unit-tested with no
  network. Rules: XFO deny/sameorigin/ALLOW-FROM → stream; CSP frame-ancestors
  present and not `*` → stream; http target under our https app (mixed
  content) → stream; else → iframe.
- **`probe_url(...)`** — async orchestration: `client.stream()` the URL and
  read ONLY the response headers (never the body — the URL is user-supplied
  and uncapped, so a full read could pin a worker), following redirects
  MANUALLY (≤5) so every hop re-passes the [[url_safety.py]] SSRF gate, then
  classify the final hop's headers. `client`/`resolver` injectable for tests
  (httpx MockTransport).

## Key decision: probe-failed → iframe (optimistic), not stream

A failed iframe embed is a visible, instantly-fixable failure (user flips
the toggle); defaulting to stream would silently burn a server-side browser
and hide that the site was actually embeddable. Default to the cheap
failure. SSRF on a *later* redirect hop also degrades to iframe (the browser,
not us, fetches the iframe) — only the *initial* URL's SSRF rejection is a
hard reject, and that happens in url_artifact.open_url, not here.

## Extensibility

`EmbedVerdict.recommended == "stream"` is the exact seam the future streaming
renderer (方案三) plugs into — this module already routes un-embeddable sites
there; today the frontend renders a fallback card for that value.
