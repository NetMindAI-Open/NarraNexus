"""
@file_name: artifacts_public.py
@author: Bin Liang
@date: 2026-05-14
@description: Public (JWT-bypassed) raw-content route for agent artifacts.

Why this exists
---------------
Multi-file HTML artifacts must be loaded into an <iframe> via a real `src` URL
(not a `blob:` URL) so the entry HTML's relative references (./style.css,
./data.json) resolve. Native iframe `src` loads cannot attach an Authorization
header, so cloud-mode JWT auth can't gate them.

This route bypasses JWT (it lives under `/api/public/`, see
`backend/auth.py::AUTH_EXEMPT_PREFIXES`) and uses an HMAC-signed view token
embedded in the path as its auth. The JWT-authed
`GET /api/agents/{aid}/artifacts/{aid}/view-token` endpoint mints the token.
Token format and verification: `backend/routes/_artifact_token.py`.

URL shape
---------
    GET /api/public/artifacts/raw/{token}/{file_path:path}

The token is in the *path*, not the query string, so the entry document's
relative sub-resource requests preserve the token prefix automatically.
"""
from __future__ import annotations

import mimetypes
import os
from typing import Optional
from urllib.parse import urlparse

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from loguru import logger

from xyz_agent_context.repository.artifact_repository import ArtifactRepository
from xyz_agent_context.settings import settings
from xyz_agent_context.utils.db_factory import get_db_client

from backend.routes._artifact_token import TokenError, verify


router = APIRouter()


SAFE_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "SAMEORIGIN",
    "Referrer-Policy": "no-referrer",
    "Cross-Origin-Resource-Policy": "same-origin",
}


def _app_origin(request: Request) -> str:
    """Best-effort browser-visible origin (scheme://host[:port]) of the app the
    artifact iframe is embedded in.

    Used to build a CSP host-source so the sandboxed (opaque-origin) iframe
    can load its own sibling assets (./style.css, ./data.json, images) while
    external hosts stay blocked.

    Priority:
    1. `settings.public_base_url` — explicit deploy-time config wins.
    2. `Referer` / `Origin` header — reflects the actual iframe parent origin,
       robust through the Vite dev proxy (changeOrigin) AND through reverse
       proxies in production.
    3. `X-Forwarded-Proto` + `X-Forwarded-Host` / `Host` — proxy headers.
    4. `request.url` — last resort, internal URL the app sees.
    """
    base = (settings.public_base_url or "").strip()
    if base:
        p = urlparse(base)
        if p.scheme and p.netloc:
            return f"{p.scheme}://{p.netloc}"

    for header in ("referer", "origin"):
        val = request.headers.get(header)
        if val:
            p = urlparse(val)
            if p.scheme and p.netloc:
                return f"{p.scheme}://{p.netloc}"

    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    host = request.headers.get("x-forwarded-host") or request.headers.get("host")
    if host:
        return f"{proto}://{host}"

    return f"{request.url.scheme}://{request.url.netloc}"


def _csp_for_html(origin: str) -> str:
    """CSP for an HTML artifact's entry document.

    Allows same-host sibling assets (so the entry HTML can reference
    ./style.css, ./app.js, ./data.json, ./img/x.png) while keeping every
    cross-origin destination blocked. The iframe stays `sandbox="allow-scripts"`
    (no allow-same-origin) so the document has an opaque origin and cannot
    touch the parent app's DOM/storage/cookies even with scripts.

    Why an explicit `{origin}` host-source and not `'self'`: in a sandboxed
    iframe without `allow-same-origin`, the document origin is opaque, and
    CSP `'self'` matches nothing. A host-source (e.g. `https://app.example`)
    matches by URL host independent of the document's origin.
    """
    return (
        f"default-src 'none'; "
        f"script-src {origin} 'unsafe-inline'; "
        f"style-src {origin} 'unsafe-inline'; "
        f"img-src {origin} data: blob:; "
        f"font-src {origin} data:; "
        f"media-src {origin} data: blob:; "
        f"connect-src {origin}; "
        f"frame-src 'none'; "
        f"object-src 'none'; "
        f"base-uri 'none'"
    )


_NON_HTML_CSP = {
    "application/vnd.echarts+json": "default-src 'none'",
    "text/csv": "default-src 'none'",
    "text/markdown": "default-src 'none'",
    "image/png": "default-src 'none'; img-src 'self'",
    "image/jpeg": "default-src 'none'; img-src 'self'",
    "application/pdf": "default-src 'none'; object-src 'self'",
}


@router.get("/raw/{token}/{file_path:path}")
async def get_raw(request: Request, token: str, file_path: str = ""):
    """Serve a file from an artifact's root directory.

    Status codes:
      - 200: file bytes
      - 401: token signature mismatch / malformed (TokenInvalid)
      - 410: token decoded but expired (TokenExpired) — frontend should
             re-mint via the `view-token` endpoint
      - 404: token valid but artifact missing, requested path outside the
             artifact root, or asset missing on disk

    `file_path` is the sub-path under the artifact root directory. Empty
    `file_path` (URL ending in `/raw/{token}/`) serves the entry file.
    """
    try:
        claims = verify(token)
    except TokenError as e:
        return JSONResponse(
            status_code=e.http_status,
            content={"error": e.__class__.__name__, "detail": str(e)},
        )

    db = await get_db_client()
    repo = ArtifactRepository(db)
    art = await repo.get_by_id(claims.artifact_id)
    if art is None or art.agent_id != claims.agent_id:
        raise HTTPException(404, "artifact not found")
    if not art.file_path:
        # Legacy (pre-pointer-model) row that was never re-registered.
        raise HTTPException(410, "artifact has no content pointer on disk")

    base = os.path.realpath(settings.base_working_path)
    entry_abs = os.path.realpath(os.path.join(base, art.file_path))
    artifact_root = os.path.dirname(entry_abs)
    if not (artifact_root == base or artifact_root.startswith(base + os.sep)):
        logger.warning(
            f"path-escape blocked: artifact={claims.artifact_id} entry={art.file_path!r}"
        )
        raise HTTPException(404, "artifact not found")

    # Single-file mode: when the entry sits directly at the agent workspace
    # root (artifact_root == workspace), the dirname tree would be the whole
    # workspace — serving siblings would expose every other file the agent
    # owns. Refuse sub-path requests in that case; only the entry serves.
    # This is the soft replacement for the old "entry must be in a
    # subdirectory" hard rule.
    workspace_root = os.path.realpath(
        os.path.join(base, f"{art.agent_id}_{art.user_id}")
    )
    if artifact_root == workspace_root and file_path:
        raise HTTPException(404, "sibling assets not served for workspace-root entries")

    if file_path:
        requested = os.path.realpath(os.path.join(artifact_root, file_path))
        if not requested.startswith(artifact_root + os.sep):
            logger.warning(
                f"path-escape blocked: artifact={claims.artifact_id} file_path={file_path!r}"
            )
            raise HTTPException(404, "not found")
        target = requested
    else:
        target = entry_abs

    if not os.path.isfile(target):
        logger.warning(f"artifact file missing on disk: {target}")
        raise HTTPException(410, "artifact file missing on disk")

    is_entry = target == entry_abs
    if is_entry:
        media_type: Optional[str] = art.kind
    else:
        media_type = mimetypes.guess_type(target)[0] or "application/octet-stream"

    if is_entry and art.kind == "text/html":
        csp = _csp_for_html(_app_origin(request))
    elif is_entry:
        csp = _NON_HTML_CSP.get(art.kind, "default-src 'none'")
    else:
        # Asset under an HTML artifact's root. CSP on a sub-resource response
        # doesn't govern further loading (only the document's CSP does), so a
        # generic safe value is fine.
        csp = "default-src 'none'"

    headers = {**SAFE_HEADERS, "Content-Security-Policy": csp}
    return FileResponse(path=target, media_type=media_type, headers=headers)
