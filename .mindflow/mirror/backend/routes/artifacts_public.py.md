---
code_file: backend/routes/artifacts_public.py
last_verified: 2026-05-22
stub: false
---

## 2026-05-22 — raw route accepts HEAD (kills 405)

The raw route is registered with `@router.api_route(..., methods=["GET","HEAD"])`,
NOT `@router.get`. FastAPI's `APIRoute` does **not** auto-add HEAD to GET routes
(plain Starlette does), so a HEAD request used to 405 at routing — before the
handler ran. The frontend `HtmlRenderer` probes this URL with
`fetch(url, {method:'HEAD'})` to detect broken pointers (410 → self-heal); the
blanket 405 made every probe fail and the self-heal never fired. Starlette's
`FileResponse` serves HEAD as headers-only, so the 200/410/401/404 mapping is
identical to GET, just without a body. Don't revert to `@router.get`.

## 2026-05-14-r3 — single-file mode when entry sits at workspace root

When `dirname(entry) == agent workspace`, any `file_path != ""` (sub-path)
request returns 404 — only the entry serves. This is the soft replacement
for the old "entry can't sit at workspace root" hard rule (which we dropped
together with `delete_source`). Without this check, a multi-file artifact
registered at the workspace root would serve Bootstrap.md and every other
artifact's files as "siblings".

# artifacts_public.py

## Why it exists

Multi-file HTML artifacts must load into an `<iframe>` with a real `src` URL
(not a `blob:` URL) so the entry HTML's relative references resolve. Native
iframe loads cannot attach Authorization headers, so cloud-mode JWT auth can't
gate them. This router lives on the JWT-exempt `/api/public/` prefix and
authenticates each request via an HMAC token embedded in the URL **path** —
so the entry document's relative sub-resource requests preserve the token
automatically.

## Endpoint

`GET|HEAD /api/public/artifacts/raw/{token}/{file_path:path}` — serve a file
from the artifact's root directory (HEAD = headers only, for the renderer
broken-pointer probe).

- Empty `file_path` → entry file.
- Non-empty → the named asset under the artifact root (realpath-confined to
  that root; path-escape attempts return 404).
- 401 / 410 / 404 / 200 mapping documented in the handler docstring.

## Upstream / Downstream

Upstream:
- Frontend `HtmlRenderer` sets the iframe `src` to the `raw_url` returned by
  `GET /api/agents/{aid}/artifacts/{aid}/view-token` (in `agents_artifacts.py`).
- Other renderers (image / pdf / csv / md / chart) `fetch()` the same URL to
  build blob URLs / text content, without needing an Authorization header.

Downstream:
- `_artifact_token.verify` for token verification (HMAC-SHA256, 2h TTL).
- `ArtifactRepository.get_by_id` to look up the artifact by `claims.artifact_id`.
- `settings.base_working_path` to resolve `art.file_path` to an absolute path.

Mounted under `/api/public/artifacts` (see `backend/main.py`).
Lives on the JWT-bypassed prefix (`backend/auth.py::AUTH_EXEMPT_PREFIXES`).

## Design decisions

**Token in the path, not the query string.** A relative URL like `./style.css`
inside the entry HTML resolves against the document URL; the resolution
preserves the path prefix but drops the query. Putting the token in the path
makes sub-resource requests pick it up "for free", which is the whole reason
multi-file HTML works under this scheme.

**Dynamic CSP host-source for HTML, via `_app_origin`.** The iframe stays
`sandbox="allow-scripts"` (no `allow-same-origin`) — the document has an
opaque origin and cannot reach the parent app's DOM/storage. In an opaque-
origin context, CSP `'self'` matches nothing, so we build a host-source from
the request: `settings.public_base_url` (explicit deploy config) → Referer
header (reflects actual browser-visible origin, works through the Vite dev
proxy with `changeOrigin: true`) → forwarded headers → request.url. The
entry HTML's CSP is what governs sub-resource loading; assets get a generic
strict CSP (CSP on a sub-resource response doesn't gate further loads).

**Asset MIME guessed via `mimetypes`** rather than the artifact's `kind` —
the kind is the *entry file's* type, not a sibling `style.css` or `data.json`.

**410 vs 404.** 410 when a row exists but the file is gone on disk
(intentional under the "live pointer" model — the agent can delete the
underlying file); 404 for "artifact missing" / "asset outside root" /
"agent mismatch on the token claim".

**No version concept.** A token claims `agent_id` + `artifact_id` + `exp`,
nothing else. The pointer is whatever `instance_artifacts.file_path` says
right now — re-registering an artifact silently swaps the served content.

## Gotchas

- The token TTL is 2h (`_artifact_token.DEFAULT_TTL_SECONDS`). A long-open
  artifact tab loading a lazy asset after expiry gets 410; the frontend
  retries by re-minting via the `view-token` endpoint.
- `_app_origin` does NOT use `'self'` because the sandboxed iframe is opaque
  origin (see the Design decision above). Don't "simplify" the CSP — it will
  break sibling-asset loading in production.
- The `Referer` header is set by the parent app page (default browser
  policy `strict-origin-when-cross-origin` sends full URL for same-origin
  sub-frames). If a future change adds a restrictive `Referrer-Policy` on
  the parent app, `_app_origin` falls back through `Origin` / forwarded /
  `Host` — robust enough.
- Path confinement uses `os.path.realpath` + `startswith(workspace + os.sep)`
  so symlinks inside the workspace can't escape.
