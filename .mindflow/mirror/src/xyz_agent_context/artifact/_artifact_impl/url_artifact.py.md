---
code_file: src/xyz_agent_context/artifact/_artifact_impl/url_artifact.py
last_verified: 2026-07-22
stub: false
---

# url_artifact.py — create / update URL-tab artifacts (application/x-url)

## Why it exists

A URL tab is a first-class pointer artifact (NOT a parallel tab system — the
office-live merge taught us "two preview concepts confuse users"). Its entry
file is a small JSON doc (`UrlArtifactDoc`) at `tabs/<slug>/page.url.json` in
the agent workspace. This module owns the doc I/O + orchestration; the actual
registration goes through the shared [[registration.py]], so URL tabs get
heal / delete / bundle / raw-serving for free. No DB column for the URL — the
JSON doc is the source of truth (pointer model preserved).

## open_url flow

1. Reject a URL whose BROWSER-origin equals our own app's
   (`_reject_self_origin`) — a same-origin URL tab would become a same-origin
   scriptable iframe (the renderer's sandbox has `allow-same-origin`) able to
   read the app token. Comparison is browser-accurate (scheme + lowercased
   host + effective port; userinfo dropped) so `AGENT…`, `host:443`, `u@host`
   can't bypass it. Candidate origins: `settings.public_base_url` PLUS the
   `app_origin` the HTTP route derives from the request (so the guard holds
   even if the config is unset); the MCP path passes only public_base_url.
   Then SSRF-gate the URL ([[url_safety.py]]) — a non-public target raises
   ArtifactError(400): tab creation fails loudly.
2. Probe embeddability ([[embed_probe.py]]) — never crashes; a failed probe
   degrades to an optimistic iframe verdict.
3. Write the doc into a DEDICATED `tabs/<slug>/` subdir — each URL tab gets
   its own artifact root so the raw route's isolation means one tab can never
   read another's json.
4. Register through the shared pointer path with `kind=application/x-url`,
   `description=url` (so the URL shows in listings / the agent's state block).

## set_embed_mode

Rewrites the on-disk doc's `embed.user_override` (the user's manual toggle,
which wins over the probe). Bumps updated_at via `repo.update_title` so the
frontend's refetch sees a change. 404s if the artifact is missing / not this
agent's / not a URL tab; a missing doc file on disk (a real pointer-model
state) raises `ArtifactContentGone` (410), not a bare FileNotFoundError → 500.

## Agent-readable content snapshot (2026-07-22)

The probe (embed verdict) and page-text capture are independent outbound
fetches — `open_url` runs them CONCURRENTLY (`asyncio.gather`) under one
`asyncio.timeout(_OPEN_FETCH_BUDGET_S)` wall-clock budget, so opening a tab
isn't the sum of both worst cases; on timeout it degrades to an optimistic
iframe verdict + no text. This budget is an outbound-HTTP deadline, NOT an
agent_loop ceiling (铁律 #14 untouched). The content filename is the schema
constant `URL_TAB_CONTENT_FILENAME` (not a private local), so the state-block
reader doesn't reach across the package seam.

`open_url` also writes `tabs/<slug>/content.md` next to the doc — a bounded
plain-text snapshot of the page ([[page_text.py]]) so the agent can SEE what
the page says. Written ALWAYS (even when extraction fails, with a "could not
capture" note) so the state-block hint in [[common_tools_module.py]] always
points at a real file. Best-effort: `fetch_page_text` never raises.

## Atomic doc write

`_write_doc` writes a sibling temp file then `os.replace` (atomic on POSIX)
so a crash mid-toggle can't leave a half-written doc that bricks the tab —
a reader always sees the old or the new complete doc.

## Gotcha

The doc is written to `registration.workspace_root(agent_id, user_id)` joined
with the relative entry — NOT to `base_working_path` directly. It MUST match
where `registration._resolve_entry` looks, or register fails "file not found".
