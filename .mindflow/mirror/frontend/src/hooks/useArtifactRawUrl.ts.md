---
code_file: frontend/src/hooks/useArtifactRawUrl.ts
last_verified: 2026-05-14
stub: false
---

# useArtifactRawUrl.ts — view-token handshake for artifact raw content

## Why it exists

Under the pointer model, artifact raw content lives at
`/api/public/artifacts/raw/{token}/{path:path}` (JWT-bypassed; HMAC token in
the path is the auth). Every renderer that wants to load an artifact's bytes
needs the same handshake:

1. JWT-authed `GET /api/agents/{aid}/artifacts/{aid}/view-token` → mint a
   short-TTL token.
2. Point an iframe / fetch at the returned directory URL ending in `/`.

This hook centralises that handshake. Renderers don't need to know what a
view token is — they ask for a URL, eventually get one.

## Upstream / Downstream

Upstream:
- All 7 artifact renderers (Html/Chart/Csv/Markdown/Image/Pdf, with
  Image+Pdf both using ImageRenderer/PdfRenderer respectively).
- `ArtifactPreviewCard` (chat-inline thumbnail).
- `ArtifactDownloadMenu` (download link).

Downstream:
- `artifactsApi.getRawUrl` (the JWT-authed token mint call).

## Design decisions

**One mint per renderer mount.** A renderer remounts when the user opens an
artifact tab; one token per mount is enough. Token TTL (2h) covers all
sub-resource loads within a typical viewing session.

**`refreshKey` for forced re-mint = `artifact.updated_at`.** All renderers
pass `artifact.updated_at` as the refreshKey. When the agent re-registers
onto the same `artifact_id` (`target_artifact_id` in the runner), the row's
`updated_at` bumps, ChatPanel refetches and upserts to the store, the
renderer sees a new prop value, and this hook re-mints a fresh token. The
iframe's `src` changes, the iframe reloads, the user sees the update.
This is the **live refresh signal** the agent triggers explicitly by
re-registering.

**No retry on expiry.** Expired tokens return 410 from the public route. A
sub-resource load that fails because the token expired is a degraded UX, not
a security event — the user can refresh the tab to mint a new token. Adding
auto-retry would complicate every renderer for a rare case.

## Gotchas

- The directory URL ends with `/`. An iframe pointed at it loads the entry
  file; relative `./style.css` references resolve as siblings under the same
  token-protected path. Don't normalise / strip the trailing slash.
- The hook returns `{ url, error }`. `url === null` is "still loading", NOT
  "no content" — renderers should render their own "Loading…" state.
