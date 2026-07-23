---
code_file: src/xyz_agent_context/utils/safe_http.py
last_verified: 2026-07-22
stub: false
---

# safe_http.py — the single SSRF-gated redirect walk

## Why it exists

The embed probe and the page-text extractor both need "GET a URL, follow
redirects, but SSRF-check every hop." That loop was copy-pasted into both
(PR #137 review flagged it) — and a duplicated security walk means the gate
can be fixed in one copy and forgotten in the other. This is the ONE
implementation; `embed_probe.probe_url` and `page_text.fetch_page_text` (and
the future RenderService / streaming browser) all build on it.

## Design

- `safe_stream_get(client, url, ...)` is an `@asynccontextmanager` that yields
  the FINAL (non-redirect) response with its body UNREAD, so the caller
  chooses: read headers only (probe) or a bounded body slice (text). Following
  redirects manually is what lets `assert_public_http_url` run on each hop.
- Lives in `utils/` (not the artifact package) so RenderService can reuse it;
  kept SEPARATE from [[url_safety.py]] so that module stays stdlib-only (its
  contract) — this one owns the httpx dependency.
- `BROWSER_UA` / `MAX_REDIRECTS` are the single source (callers stopped each
  keeping a private copy). `RedirectLimitError` signals the loop overran;
  callers catch it and degrade.

## Gotcha

The `client` MUST be `follow_redirects=False` — we follow manually. Passing a
client that auto-follows would bypass the per-hop SSRF gate.
