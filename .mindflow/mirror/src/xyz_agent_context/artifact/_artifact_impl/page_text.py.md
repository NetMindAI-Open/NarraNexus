---
code_file: src/xyz_agent_context/artifact/_artifact_impl/page_text.py
last_verified: 2026-07-22
stub: false
---

## 2026-07-22 — built on the shared safe_http walk

`fetch_page_text` no longer carries its own redirect+SSRF loop / UA / redirect
constant (that was a near-duplicate of the probe's). It opens
[[safe_http.py]] `safe_stream_get` and reads a BOUNDED slice of the final
hop's body. `open_url` runs it CONCURRENTLY with the probe (asyncio.gather)
under one wall-clock budget — see [[url_artifact.py]].

# page_text.py — readable-text snapshot of a URL tab (agent visibility)

## Why it exists

A URL tab should let the agent SEE the page's content, not just know a tab
exists. This fetches the page server-side and extracts a bounded plain-text
snapshot; `open_url` ([[url_artifact.py]]) writes it to the tab's `content.md`,
which the artifact state block ([[common_tools_module.py]]) points the agent
at. This is the cheap TEXT-level answer to "agent can't see the page" — the
VISUAL (rendered-screenshot) version is the heavier RenderService (方案三),
deliberately not built here.

## Two parts

- **`html_to_text(html)`** — pure, dependency-free HTML→text (strip
  script/style/noscript/template, turn block-ends into line breaks, drop tags,
  unescape entities, collapse whitespace). Not a real parser — just the gist.
- **`fetch_page_text(url)`** — async, best-effort. Follows redirects MANUALLY
  so every hop re-passes the [[url_safety.py]] SSRF gate; reads the body with
  a HARD byte cap (`_MAX_FETCH_BYTES`, stops mid-stream); skips non-text
  content types; caps output (`_MAX_TEXT_CHARS`). NEVER raises — any failure
  (network, SSRF hop, non-HTML, empty) returns None, so it can't break tab
  creation.

## 2026-07-22 — strip truncated-mid-block script/style (+ comments)

When the body is byte-capped mid-`<style>`/`<script>`, the closing tag is never
read so the complete-block regex can't remove it and the raw CSS/JS leaked into
the "text" (baidu et al. front-load huge inline `<style>`). `html_to_text` now:
(1) strips HTML comments FIRST (a commented-out `<script>` would otherwise look
like an orphan, and a raw `<!--` leaks into text); (2) tolerates `</script >`
(space) in the complete-block close; (3) for the truncated tail, cuts at the
LAST orphan open tag only — anchoring on the FIRST orphan (an earlier attempt)
silently DELETED all body after any mid-document orphan, which is worse than
CSS noise for "let the agent see the page". Regression tests pin all three.

## Gotchas

- The snapshot is taken at open time; if the page changes later it's stale
  (same tradeoff as the embed verdict). Re-opening refreshes it.
- Byte cap + char cap are both load-bearing: the URL is user/agent-supplied,
  so an unbounded read would be a resource-exhaustion vector (same lesson as
  the streaming-headers-only embed probe).
