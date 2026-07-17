"""
@file_name: office_watch_proxy.py
@author: NetMind.AI
@date: 2026-07-13
@description: Reverse-proxy for the live Office-document preview.

An office document registered as an artifact renders live. The `/office-watch/
open` endpoint ensures an `officecli watch` server is running for it (locally:
spawned by the backend; cloud: via the executor's `/watch/ensure`) and mints a
signed iframe URL. That server renders the document as auto-refreshing HTML and
pushes updates over SSE (`/events`). The public proxy lets the browser reach it
same-origin:

  browser  ──►  GET /api/office-watch-proxy/{port}/{path}  (this route)
                     │  local  → http://127.0.0.1:{port}/{path}
                     │  cloud  → {executor_url}/watch/{port}/{path}
                     ▼
                officecli watch server

Streaming discipline mirrors `manyfold_files.read_file` (StreamingResponse +
`X-Accel-Buffering: no`) and the long-lived-stream client config from
`remote_agent_loop_driver` (`aiohttp.ClientTimeout(total=None)`), so SSE frames
flow through unbuffered.

Security:
  * the authed ``/api/office-watch/open`` endpoint (session auth) mints a
    signed token; the public proxy (``/api/public/office-watch-proxy/{token}``)
    is token-authed so an <iframe> navigation — which can't send X-User-Id —
    still carries auth in the path. Same pattern as artifacts.
  * port allowlist (``is_watch_port``) rejected before dialing — prevents this
    from becoming an SSRF into other in-container ports (executor :8020,
    sqlite proxy :8100, ...).
  * cloud cross-user isolation is automatic: ``ensure_executor(user_id)`` only
    ever returns THAT user's container URL, so a user can never reach another
    user's watch server.
"""

from __future__ import annotations

import asyncio
import itertools
import os
import re
from urllib.parse import quote

import aiohttp
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from loguru import logger

from backend.auth import resolve_current_user_id
from backend.routes import _office_watch_token as office_watch_token
from backend.routes._artifact_token import TokenError
from xyz_agent_context.agent_framework.broker_client import ensure_executor, wait_until_ready
from xyz_agent_context.utils.deployment_mode import is_cloud_mode
from xyz_agent_context.utils.office_watch import (
    OFFICE_LIVE_KIND,
    ensure_watch,
    is_watch_port,
    resolve_watch_file,
)

# Authed router (mounted at /api): mints the signed iframe URL.
router = APIRouter()
# Public router (mounted at /api/public, auth-exempt): the token IS the auth.
public_router = APIRouter()

# The officecli watch page hardcodes ROOT-ABSOLUTE URLs — `new
# EventSource('/events')`, `fetch('/')`, `fetch('/api/send')`, and static
# `href="/assets/..."`. Served under our sub-path proxy those would resolve to
# the SPA origin root and break (no live refresh). We rewrite the document so
# every root-absolute reference resolves back through THIS proxy prefix:
#   - inject <base href="{prefix}/"> so relative URLs resolve under the prefix
#   - inject a shim that strips the leading slash from EventSource/fetch args
#     (root-absolute → relative → resolved against <base>)
#   - rewrite static src|href|action="/..." attributes to the prefix
# Only the text/html document is rewritten; SSE (/events) and assets stream
# through untouched.
_ABS_ATTR_RE = re.compile(r'(\b(?:src|href|action)\s*=\s*")/(?!/)')


def _rewrite_watch_html(html: str, prefix: str) -> str:
    """Rebase a watch page's root-absolute URLs onto the proxy prefix + tidy the
    preview chrome (hide speaker notes)."""
    shim = (
        f'<base href="{prefix}/">'
        # A live preview shows the SLIDES, not speaker notes. The watch page's
        # `.slide-notes` is a fixed `width: <design-w>` block that sticks out
        # wider than the scaled slide — hide it for a clean preview.
        "<style>.slide-notes{display:none!important;}</style>"
        "<script>(function(){"
        "var rebase=function(u){return (typeof u==='string'&&u.charAt(0)==='/'&&u.charAt(1)!=='/')?u.slice(1):u;};"
        "var OE=window.EventSource;"
        "if(OE){window.EventSource=function(u,o){"
        "var es=new OE(rebase(u),o);"
        # Tell the parent (OfficeWatchViewer) when a real CONTENT change rendered
        # over SSE, so its mtime-poll fallback stays quiet — no double-render
        # flicker while live-refresh is working. Ignore cursor (selection/mark)
        # frames + heartbeats.
        "var notify=function(ev){try{var d=JSON.parse((ev&&ev.data)||'{}');var a=d.action||'';"
        "if(a&&a!=='selection-update'&&a!=='mark-update'){parent.postMessage({type:'officewatch-content'},'*');}}catch(e){}};"
        "es.addEventListener('update',notify);es.addEventListener('message',notify);"
        "return es;};window.EventSource.prototype=OE.prototype;}"
        "var of=window.fetch;"
        "if(of){window.fetch=function(u,o){return of(rebase(u),o);};}"
        # The watch page scales the slide (transform: scale) by measuring the
        # container ONCE at load (clientWidth/innerWidth), and only re-scales on
        # a window `resize`. In the desktop WKWebView the sandboxed iframe often
        # isn't at its final size yet when that first measure runs, so the slide
        # scales wrong and CJK text crams one glyph per line — it looks vertical
        # until something triggers a re-measure. Nudge a few resize events after
        # load so it re-scales against the settled container. Harmless in the
        # browser (re-scales to the same size).
        "var nudge=function(){try{window.dispatchEvent(new Event('resize'));}catch(e){}};"
        "[100,300,700,1500].forEach(function(t){setTimeout(nudge,t);});"
        "})();</script>"
    )
    # Rewrite static absolute attrs first, then inject <base>+shim as early as
    # possible so it patches EventSource/fetch before the page's own scripts run.
    html = _ABS_ATTR_RE.sub(rf"\1{prefix}/", html)
    if "<head>" in html:
        return html.replace("<head>", "<head>" + shim, 1)
    if "<html" in html:
        # inject right after the opening <html ...> tag
        return re.sub(r"(<html[^>]*>)", r"\1" + shim.replace("\\", "\\\\"), html, count=1)
    return shim + html


# The preview iframe uses sandbox="allow-scripts" (opaque origin), so the watch
# page's own EventSource('/events') + fetch() are treated as CROSS-origin and
# CORS-gated. Without this the SSE stream is silently blocked → the preview
# renders once but never live-refreshes. `*` is safe here: auth is the signed
# token in the URL path, not a cookie, and EventSource is non-credentialed.
_CORS_HEADERS = {"Access-Control-Allow-Origin": "*"}

# Request headers worth forwarding upstream (SSE negotiation + conditional
# reload). We deliberately do NOT forward Host/Authorization/Cookie.
_FORWARD_REQUEST_HEADERS = ("accept", "cache-control", "last-event-id")
# Response headers worth copying back (content type + SSE cache semantics).
_COPY_RESPONSE_HEADERS = ("content-type", "cache-control")

# Cap concurrent live-preview SSE streams PER USER on this shared backend. Each
# open preview tab holds one long-lived `/events` stream; without a cap a user
# (or a leaked token) could pile up streams and exhaust backend connections/fds.
# When a user's (N+1)th stream opens we evict their OLDEST — close its upstream
# session, which ends that stream's body generator and drops the connection (the
# browser's EventSource sees the close). Per-user so one user can never evict
# another's stream. Short-lived asset requests aren't event-streams, so they
# don't count. Set generously so normal use (a few open previews) never evicts.
MAX_SSE_STREAMS_PER_USER = 8
_stream_lock = asyncio.Lock()
_stream_seq = itertools.count()
_active_streams: dict[int, dict] = {}  # seq (monotonic, = age order) -> {user_id, session}


async def _register_sse_stream(user_id: str, session: aiohttp.ClientSession) -> int:
    """Register a live SSE stream, evicting this user's oldest over the cap.

    Returns the stream id; pass it to `_unregister_sse_stream` in the body's
    finally. Eviction closes the victim's aiohttp session — that ends its body
    generator (its `async for` over the upstream stops) and closes the response.
    """
    async with _stream_lock:
        mine = sorted(sid for sid, e in _active_streams.items() if e["user_id"] == user_id)
        # Keep at most MAX-1 existing, so this new one brings the total to MAX.
        for victim in mine[: max(0, len(mine) - (MAX_SSE_STREAMS_PER_USER - 1))]:
            entry = _active_streams.pop(victim, None)
            if entry is not None:
                try:
                    await entry["session"].close()
                except Exception:  # noqa: BLE001 — best-effort eviction
                    pass
        sid = next(_stream_seq)
        _active_streams[sid] = {"user_id": user_id, "session": session}
        return sid


def _unregister_sse_stream(sid: int) -> None:
    _active_streams.pop(sid, None)


async def _resolve_upstream(user_id: str, port: int, path: str, query: str) -> str:
    """Build the upstream URL for the watch server, per run mode.

    Local/desktop (no broker) → the watch server is on this host.
    Cloud → it lives inside the user's executor container; reach it via the
    executor's ``/watch`` passthrough endpoint.
    """
    suffix = f"{port}/{path}"
    if query:
        suffix += f"?{query}"
    ensured = await ensure_executor(user_id)
    if ensured is not None:
        return f"{ensured.url.rstrip('/')}/watch/{suffix}"
    return f"http://127.0.0.1:{suffix}"


async def _ensure_watch_in_executor(user_id: str, agent_id: str, rel: str) -> int | None:
    """Cloud: ask the user's executor to start an officecli watch in-container
    and return the port it ALLOCATED to this file (None if it couldn't start).

    The executor owns port allocation (that's where the watches run), so the
    port is decided there and returned — the orchestrator never guesses it.

    Raises HTTPException(503) only when the executor itself is unavailable.
    """
    ensured = await ensure_executor(user_id)
    if ensured is None:
        raise HTTPException(status_code=503, detail="executor unavailable")
    await wait_until_ready(ensured.url)
    body = {"agent_id": agent_id, "user_id": user_id, "file": rel}
    timeout = aiohttp.ClientTimeout(total=30)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(f"{ensured.url.rstrip('/')}/watch/ensure", json=body) as r:
                if r.status != 200:
                    return None
                data = await r.json()
                port = data.get("port")
                return int(port) if port is not None else None
    except aiohttp.ClientError as e:
        logger.warning(f"office-watch: executor watch/ensure failed: {e}")
        return None


async def _lookup_office_file(request: Request, artifact_id: str) -> tuple[str, str, str, str]:
    """Resolve + authorize an office-live artifact and its workspace file.

    Shared by `open` and `version`. Returns
    ``(user_id, agent_id, abs_path, rel)`` where ``rel`` is the workspace-
    relative POSIX path (the exact string the watch is spawned with).

    Raises:
        HTTPException: 404 if missing/foreign, 400 if not an office artifact or
            the file escapes the workspace / is the wrong type.
    """
    from xyz_agent_context.repository.artifact_repository import ArtifactRepository
    from xyz_agent_context.utils.db_factory import get_db_client
    from xyz_agent_context.utils.workspace_paths import resolve_workspace_relative_file

    user_id = await resolve_current_user_id(request)
    repo = ArtifactRepository(await get_db_client())
    art = await repo.get_by_id(artifact_id)
    if art is None or art.user_id != user_id:
        raise HTTPException(status_code=404, detail="artifact not found")
    if art.kind != OFFICE_LIVE_KIND:
        raise HTTPException(status_code=400, detail="artifact is not an office document")

    abs_path = str(resolve_workspace_relative_file(art.file_path, art.agent_id, user_id))
    try:
        rel = resolve_watch_file(art.agent_id, user_id, abs_path)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return user_id, art.agent_id, abs_path, rel


@router.get("/office-watch/open")
async def office_watch_open(request: Request, artifact_id: str) -> dict:
    """Open (or re-open) the live preview for an OFFICE artifact.

    Unified with the artifact system: the office doc was registered as an
    artifact, so the caller passes ``artifact_id``. We look it up, confirm it
    belongs to the requesting user + is the office-live kind, resolve its
    workspace file, ENSURE a watch is running on a port DEDICATED to this file
    (restarting it if it idle-stopped / died — what makes refresh & reopen
    reliable), then mint the signed iframe URL for the port it was given.

    Session-authed (the SPA sends X-User-Id). The returned URL embeds a signed
    token so the <iframe> navigation + the page's own sub-requests carry auth
    in the path.

    Ensuring the watch: local/desktop spawns it in the backend (co-located with
    the workspace); cloud asks the user's executor to spawn it in-container (via
    `/watch/ensure`), since that's where the workspace + officecli edits live.
    """
    user_id, agent_id, _abs, rel = await _lookup_office_file(request, artifact_id)

    # Ensure a watch is running and get the port ALLOCATED to this file (a
    # dedicated port per document — see office_watch._allocate_port — so opening
    # several docs at once never cross-wires one tab onto another's watch).
    if is_cloud_mode():
        # Cloud: the watch must run inside the user's executor container (where
        # the workspace + the agent's officecli edits live). Ask the executor
        # to start it, then the proxy forwards to `{executor}/watch/{port}`.
        port = await _ensure_watch_in_executor(user_id, agent_id, rel)
    else:
        port = await asyncio.get_running_loop().run_in_executor(None, ensure_watch, agent_id, user_id, rel)
    if port is None:
        raise HTTPException(status_code=503, detail="could not start preview server")

    token = office_watch_token.mint(user_id=user_id, port=port)
    # Trailing slash so the injected <base> resolves the page's relatives.
    raw_url = f"/api/public/office-watch-proxy/{token}/{port}/"
    return {"raw_url": raw_url, "port": port}


async def _file_version_in_executor(user_id: str, agent_id: str, rel: str) -> dict:
    """Cloud: ask the user's executor for the office file's mtime+size."""
    ensured = await ensure_executor(user_id)
    if ensured is None:
        raise HTTPException(status_code=503, detail="executor unavailable")
    q = f"agent_id={quote(agent_id)}&user_id={quote(user_id)}&file={quote(rel)}"
    timeout = aiohttp.ClientTimeout(total=15)
    try:
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.get(f"{ensured.url.rstrip('/')}/watch/version?{q}") as r:
                if r.status != 200:
                    raise HTTPException(status_code=404, detail="file not found")
                return await r.json()
    except aiohttp.ClientError as e:
        logger.warning(f"office-watch: executor watch/version failed: {e}")
        raise HTTPException(status_code=503, detail="version unavailable")


@router.get("/office-watch/version")
async def office_watch_version(request: Request, artifact_id: str) -> dict:
    """Lightweight change-signal for the live preview: the office file's mtime +
    size.

    The frontend polls this. When it advances but no content SSE frame arrived
    (officecli's per-file resident wasn't shared — e.g. the agent edited via a
    different path string — so the watch never live-refreshed), the viewer
    reloads the iframe. This is the correctness fallback behind the smooth SSE
    path; the watch page's own GET always renders the current document.
    """
    user_id, agent_id, abs_path, rel = await _lookup_office_file(request, artifact_id)
    if is_cloud_mode():
        return await _file_version_in_executor(user_id, agent_id, rel)
    try:
        st = os.stat(abs_path)
    except OSError:
        raise HTTPException(status_code=404, detail="file not found")
    return {"mtime": st.st_mtime, "size": st.st_size}


async def _proxy_stream(user_id: str, port: int, path: str, query: str, prefix: str, fwd_headers: dict):
    """Shared upstream fetch + HTML-rebase / SSE-stream. `prefix` is the
    browser-facing path the watch page's absolute URLs get rebased onto."""
    upstream = await _resolve_upstream(user_id, port, path, query)
    timeout = aiohttp.ClientTimeout(total=None, sock_read=None, sock_connect=10)
    session = aiohttp.ClientSession(timeout=timeout)
    try:
        resp = await session.get(upstream, headers=fwd_headers)
    except aiohttp.ClientError as e:
        await session.close()
        logger.warning(f"office-watch proxy upstream failed ({upstream}): {e}")
        raise HTTPException(status_code=502, detail="watch server unavailable")

    media_type = resp.headers.get("Content-Type", "application/octet-stream")
    if "text/html" in media_type.lower():
        try:
            raw = await resp.text()
        finally:
            resp.release()
            await session.close()
        return Response(
            content=_rewrite_watch_html(raw, prefix),
            status_code=resp.status,
            media_type=media_type,
            headers={"X-Accel-Buffering": "no", **_CORS_HEADERS},
        )

    # Only long-lived SSE streams count toward the per-user cap; asset requests
    # are short and finish on their own. Register (maybe evicting the user's
    # oldest) before streaming; unregister when the body ends.
    is_sse = "text/event-stream" in media_type.lower()
    stream_id = await _register_sse_stream(user_id, session) if is_sse else None

    async def _body():
        try:
            async for chunk in resp.content.iter_any():
                yield chunk
        finally:
            resp.release()
            await session.close()
            if stream_id is not None:
                _unregister_sse_stream(stream_id)

    headers = {"X-Accel-Buffering": "no", **_CORS_HEADERS}
    for h in _COPY_RESPONSE_HEADERS:
        if h in resp.headers and h != "content-type":
            headers[h] = resp.headers[h]
    return StreamingResponse(_body(), status_code=resp.status, media_type=media_type, headers=headers)


@public_router.get("/office-watch-proxy/{token}/{port}/{path:path}")
async def proxy_office_watch(token: str, port: int, path: str, request: Request):
    """Public (token-authed) reverse-proxy to an officecli watch server.

    The signed token carries the user_id + authorized port — an <iframe> and
    the page's own EventSource/fetch sub-requests all carry it in the path, so
    no header is needed. Cloud cross-user isolation is automatic: the token's
    user_id resolves that user's executor via ensure_executor.
    """
    try:
        claims = office_watch_token.verify(token)
    except TokenError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))

    if port != claims.port or not is_watch_port(port):
        raise HTTPException(status_code=403, detail="port not allowed for this token")

    fwd_headers = {k: v for k, v in request.headers.items() if k.lower() in _FORWARD_REQUEST_HEADERS}
    prefix = f"/api/public/office-watch-proxy/{token}/{port}"
    return await _proxy_stream(claims.user_id, port, path, request.url.query, prefix, fwd_headers)
