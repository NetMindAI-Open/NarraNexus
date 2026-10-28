"""
@file_name: proxy.py
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
`loop.remote_driver` (`aiohttp.ClientTimeout(total=None)`), so SSE frames
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
import json
import os
import re
import time
from urllib.parse import quote

import aiohttp
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, StreamingResponse
from loguru import logger

from backend.auth import resolve_current_user_id
from backend.middleware.body_size import MAX_OFFICE_EDIT_BYTES
from backend.routes.office_watch import _token as office_watch_token
from backend.routes.artifacts._token import TokenError
from narranexus.platform.agent_framework.loop.broker_client import ensure_executor, wait_until_ready
from narranexus.platform.artifact import office_lock_present
from narranexus.platform.utils.deployment_mode import is_cloud_mode
from narranexus.platform.utils.office_watch import (
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
        # Mirror the page's own selection reports to the parent
        # (OfficeWatchViewer): the page POSTs {'paths': [...]} to
        # api/selection whenever the user (de)selects elements — that payload
        # IS the anchor set the T1 edit bar operates on (data-path anchors,
        # spec B §3.3). Pure mirroring; the POST still goes through.
        "if(of){window.fetch=function(u,o){"
        "try{if(o&&o.method==='POST'&&typeof u==='string'&&u.indexOf('api/selection')!==-1&&typeof o.body==='string'){"
        "parent.postMessage({type:'officewatch-selection',body:o.body},'*');}}catch(e){}"
        "return of(rebase(u),o);};}"
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
# When a user's (N+1)th stream opens we evict their LEAST-RECENTLY-ACTIVE —
# close its upstream session, which ends that stream's body generator and drops
# the connection (the browser's EventSource sees the close). Per-user so one
# user can never evict another's stream. Short-lived asset requests aren't
# event-streams, so they don't count. Set generously so normal use (a few open
# previews) never evicts.
MAX_SSE_STREAMS_PER_USER = 8
_stream_lock = asyncio.Lock()
_stream_seq = itertools.count()  # ids only; NOT the eviction order (that is last_active)
_active_streams: dict[int, dict] = {}  # id -> {user_id, session, last_active}


async def _register_sse_stream(user_id: str, session: aiohttp.ClientSession) -> int:
    """Register a live SSE stream, evicting this user's STALEST over the cap.

    Returns the stream id; pass it to `_unregister_sse_stream` in the body's
    finally. Eviction closes the victim's aiohttp session — that ends its body
    generator (its `async for` over the upstream stops) and closes the response.

    Eviction is by LEAST-RECENT ACTIVITY (`last_active`, touched as frames
    flow), not by age: a healthy long-open preview — e.g. the column stream
    that stays live while its fullscreen modal opens a second one for the same
    doc — must not lose its slot to a newer but idle stream. Stalest first
    approximates "most likely already dead".
    """
    now = time.monotonic()
    async with _stream_lock:
        mine = [(e["last_active"], sid) for sid, e in _active_streams.items() if e["user_id"] == user_id]
        mine.sort()  # ascending last_active → stalest first
        # Keep at most MAX-1 existing, so this new one brings the total to MAX.
        for _, victim in mine[: max(0, len(mine) - (MAX_SSE_STREAMS_PER_USER - 1))]:
            entry = _active_streams.pop(victim, None)
            if entry is not None:
                try:
                    await entry["session"].close()
                except Exception:  # noqa: BLE001 — best-effort eviction
                    pass
        sid = next(_stream_seq)
        _active_streams[sid] = {"user_id": user_id, "session": session, "last_active": now}
        return sid


def _touch_sse_stream(sid: int) -> None:
    """Mark a stream active (called as frames flow) so eviction spares it."""
    entry = _active_streams.get(sid)
    if entry is not None:
        entry["last_active"] = time.monotonic()


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
    from narranexus.platform.repository.artifact_repository import ArtifactRepository
    from narranexus.platform.utils.db.db_factory import get_db_client
    from narranexus.platform.utils.workspace_paths import resolve_workspace_relative_file

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


# The ONLY public POST the proxy forwards: the watch PAGE's own selection
# report (benign server-side selection state, no document writes). The edit
# verbs (api/send / api/batch) moved to the session-authed
# /office-watch/edit endpoint (review #334 I14) — the 2-hour view token in
# the URL path must never grant write capability: path tokens land in access
# logs / history / Referer, and leaking one used to mean "can rewrite the
# document for 2 hours".
EDIT_POST_ALLOWLIST = frozenset({"api/selection"})
# Single-sourced with the middleware gate (r4 I2): the cap and the
# declared-length door can only move together.
MAX_EDIT_POST_BYTES = MAX_OFFICE_EDIT_BYTES


async def _reject_oversized_edit(request: Request) -> None:
    """Declared-length fast reject BEFORE Starlette buffers the JSON body —
    the same dual-gate shape as every other write entrance (review #334 r2
    I1: the I14 endpoint was the one place the I3 invariant didn't hold)."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_EDIT_POST_BYTES:
        raise HTTPException(status_code=413, detail="edit payload too large")


@router.post("/office-watch/edit", dependencies=[Depends(_reject_oversized_edit)])
async def office_watch_edit(request: Request, artifact_id: str) -> dict:
    """Run officecli edit commands on an office artifact's watch server.

    Session-authed (same auth as /office-watch/open — the SPA calls this from
    inside the app, so unlike the iframe there is no reason to ride the
    path-token): resolves the artifact to ITS user's watch (ensuring one is
    running), POSTs the batch to the watch server's /api/batch, and returns
    the upstream verdict. The view token stays a pure READ credential.

    A same-origin backend hop also sidesteps the desktop WKWebView
    mixed-content block that a direct browser→watch POST would hit.

    The body is read STREAMED with an accumulation cap (Content-Length can
    lie or be absent), then parsed here — never pre-buffered by the
    framework.
    """
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_EDIT_POST_BYTES:
            raise HTTPException(status_code=413, detail="edit payload too large")
        chunks.append(chunk)
    try:
        commands = json.loads(b"".join(chunks))
    except ValueError:
        raise HTTPException(status_code=422, detail="body must be a JSON array")
    if not isinstance(commands, list):
        raise HTTPException(status_code=422, detail="body must be a JSON array")
    body = json.dumps(commands).encode("utf-8")
    user_id, agent_id, _abs, rel = await _lookup_office_file(request, artifact_id)
    if is_cloud_mode():
        port = await _ensure_watch_in_executor(user_id, agent_id, rel)
    else:
        port = await asyncio.get_running_loop().run_in_executor(
            None, ensure_watch, agent_id, user_id, rel
        )
    if port is None:
        raise HTTPException(status_code=503, detail="could not start preview server")
    status, media_type, payload = await _post_upstream(
        user_id, port, "api/batch", "", body, "application/json"
    )
    if status != 200:
        # Log the upstream evidence server-side, pass only the STATUS class
        # through (r2 M7): "officecli doesn't know this verb" (4xx) and
        # "watch server is down" (5xx→502) must be distinguishable in
        # incident triage, but the upstream body can carry absolute
        # workspace paths and stays out of the client response.
        logger.warning(
            f"office-watch edit rejected upstream (status={status}): "
            f"{payload[:200]!r}"
        )
        if 400 <= status < 500:
            raise HTTPException(status_code=status, detail="watch server rejected the edit")
        raise HTTPException(status_code=502, detail="watch server unavailable")
    try:
        return json.loads(payload)
    except ValueError:
        raise HTTPException(status_code=502, detail="watch server returned non-JSON")


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
        # Cloud workspaces have no desktop Office reaching in — lock is a
        # local-disk concept, reported false there.
        return {**(await _file_version_in_executor(user_id, agent_id, rel)), "lock": False}
    try:
        st = os.stat(abs_path)
    except OSError:
        raise HTTPException(status_code=404, detail="file not found")
    # `lock` = a desktop Office app holds the ~$ owner-lock on the file; the
    # viewer greys its direct-edit bar on it (officecli writes would race the
    # desktop app's saves).
    return {"mtime": st.st_mtime, "size": st.st_size, "lock": office_lock_present(abs_path)}


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
                if stream_id is not None:
                    _touch_sse_stream(stream_id)  # keep an active stream off the evict list
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




async def _post_upstream(
    user_id: str, port: int, path: str, query: str, body: bytes, content_type: str
) -> tuple[int, str, bytes]:
    """POST ``body`` to the watch server and return (status, media_type, bytes).
    Module-level (not inline in the handler) so tests can stub the network
    seam while exercising the real gate logic."""
    upstream = await _resolve_upstream(user_id, port, path, query)
    timeout = aiohttp.ClientTimeout(total=30, sock_connect=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        try:
            resp = await session.post(
                upstream, data=body, headers={"Content-Type": content_type}
            )
            payload = await resp.read()
            return resp.status, resp.headers.get("Content-Type", "application/json"), payload
        except aiohttp.ClientError as e:
            logger.warning(f"office-watch POST upstream failed ({upstream}): {e}")
            raise HTTPException(status_code=502, detail="watch server unavailable")


@public_router.post("/office-watch-proxy/{token}/{port}/{path:path}")
async def proxy_office_watch_post(token: str, port: int, path: str, request: Request):
    """Token-authed POST passthrough for the watch server's edit APIs.

    Three gates stack: the signed token (user + authorized port), the watch
    port allowlist (SSRF guard, same as GET), and the exact-path allowlist
    above. The body is capped — an edit command is a small JSON payload.
    """
    try:
        claims = office_watch_token.verify(token)
    except TokenError as e:
        raise HTTPException(status_code=e.http_status, detail=str(e))
    if port != claims.port or not is_watch_port(port):
        raise HTTPException(status_code=403, detail="port not allowed for this token")
    if path not in EDIT_POST_ALLOWLIST:
        raise HTTPException(status_code=405, detail="POST not allowed on this path")

    # Two size gates (review #334 I3): a declared-length fast reject BEFORE
    # any byte is read, then a streamed accumulation cap — Content-Length can
    # lie or be absent (chunked), so neither gate alone is enough.
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_EDIT_POST_BYTES:
        raise HTTPException(status_code=413, detail="edit payload too large")
    chunks: list[bytes] = []
    received = 0
    async for chunk in request.stream():
        received += len(chunk)
        if received > MAX_EDIT_POST_BYTES:
            raise HTTPException(status_code=413, detail="edit payload too large")
        chunks.append(chunk)
    body = b"".join(chunks)

    status, media_type, payload = await _post_upstream(
        claims.user_id,
        port,
        path,
        request.url.query,
        body,
        request.headers.get("Content-Type", "application/json"),
    )
    return Response(content=payload, status_code=status, media_type=media_type, headers=_CORS_HEADERS)


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
