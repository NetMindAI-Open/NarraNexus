"""
@file_name: body_size.py
@author: NetMind.AI
@date: 2026-08-20
@description: The ONE layer where a declared-length body cap can actually
run before any byte is buffered.

FastAPI reads and parses the request body BEFORE it resolves route
dependencies (fastapi/routing.py: `await request.body()` /
`await request.form()` happen ahead of `solve_dependencies`), so a
`Depends(...)` size gate on a route with a body field executes only after
the framework has already parked the whole payload in memory — a
structurally fake door (review #334 r3 I1; two of the three r1-I3 gates
were exactly this). HTTP middleware wraps the app, so this check fires
before the framework touches the stream.

Per-route caps, NOT one global number: the write entrances span three
orders of magnitude (64KB edit commands vs 25MB document content) and a
single limit is necessarily wrong on one side. Content-Length can lie or
be absent (chunked) — the in-handler streamed accumulation caps stay as
the second gate wherever the handler owns the stream; for UploadFile
routes the framework spools to disk and THIS declared-length check plus
the disk are the honest bound.
"""

from __future__ import annotations

import re
from typing import List, Tuple

from fastapi import Request
from fastapi.responses import JSONResponse

from narranexus.platform.artifact import MAX_ARTIFACT_BYTES

_MB = 1024 * 1024

# Authoritative per-entrance limits (review #334 r4 I2): the routes import
# THESE (routes → middleware is the safe direction; the middleware must
# never import routes), so a cap and its gate can only move together.
#: officecli edit commands are small JSON; anything bigger is not an edit.
MAX_OFFICE_EDIT_BYTES = 64 * 1024
#: one uploaded image for a T2 replace.
MAX_OFFICE_ASSET_BYTES = 10 * _MB
#: JSON quoting/escaping margin on top of the artifact content cap — a
#: 25 MB document's JSON-encoded body is legitimately larger than 25 MB.
PUT_CONTENT_MARGIN = 2 * _MB
#: multipart boundary/part-header overhead on top of the single-file cap —
#: the declared Content-Length covers the whole multipart body, not the file.
MULTIPART_FRAMING_MARGIN = _MB
#: OpenAI-compat chat body (env-gated route: ENABLE_MANYFOLD_API). The
#: messages array is caller-supplied and unbounded — the one body-field
#: route whose payload legitimately grows without limit, i.e. exactly what
#: this middleware exists for. 8 MB of text is far beyond any real model
#: context; a bigger body is not a conversation (review #334 r6 I1).
MAX_CHAT_COMPLETIONS_BYTES = 8 * _MB

#: apply-to-agents body is {"slots": ["agent","helper_llm"]} — a tiny fixed
#: shape (the Pydantic max_length caps the element count, but that only runs
#: after buffering; this is the byte cap that runs in the middleware, before
#: FastAPI parks the body in memory). 4 KB is two orders of magnitude of slack.
MAX_APPLY_TO_AGENTS_BYTES = 4 * 1024
# Plugin factory writes: install/bisect are a few scalar fields; the error
# report carries a message (≤ 4000 chars) plus a stack (≤ 20000 chars) with
# JSON framing — 64 KiB covers the largest legitimate body with margin.
MAX_PLUGIN_FACTORY_BYTES = 64 * 1024
# Generic channel routes (plugin platform batch 4b): a bind body is a handful
# of credential fields; 64 KiB leaves room for long tokens and select options.
MAX_CHANNEL_BIND_BYTES = 64 * 1024
# Inbound channel webhooks carry a platform's event envelope (a message plus
# metadata); 256 KiB covers rich events without letting a push flood the inbox.
MAX_CHANNEL_WEBHOOK_BYTES = 256 * 1024
# Browser policy and runtime preferences carry an origin or a few enum fields.
MAX_BROWSER_SETTINGS_BYTES = 4 * 1024

#: (methods, path regex, max declared bytes). First match wins.
BODY_CAPS: List[Tuple[frozenset, re.Pattern, int]] = [
    (
        frozenset({"PUT", "POST"}),
        re.compile(r"^/api/browser/policy/[^/]+(?:/revoke)?$"),
        MAX_BROWSER_SETTINGS_BYTES,
    ),
    (
        frozenset({"PUT"}),
        re.compile(r"^/api/browser/runtime/(?:source|mode)$"),
        MAX_BROWSER_SETTINGS_BYTES,
    ),
    (
        frozenset({"POST"}),
        re.compile(r"^/api/providers/slots/apply-to-agents$"),
        MAX_APPLY_TO_AGENTS_BYTES,
    ),
    (
        # per-agent capability switch: {"enabled": bool} (plugin platform batch 5c)
        frozenset({"PUT"}),
        re.compile(r"^/api/agents/[^/]+/capabilities/[^/]+$"),
        MAX_APPLY_TO_AGENTS_BYTES,
    ),
    (
        frozenset({"POST"}),
        re.compile(r"^/api/plugin-factory(/|$)"),
        MAX_PLUGIN_FACTORY_BYTES,
    ),
    (
        frozenset({"POST"}),
        re.compile(r"^/api/channels/[^/]+/(bind|test|unbind|set-active)$"),
        MAX_CHANNEL_BIND_BYTES,
    ),
    (
        frozenset({"POST"}),
        re.compile(r"^/api/channels/[^/]+/webhook/[^/]+$"),
        MAX_CHANNEL_WEBHOOK_BYTES,
    ),
    (
        frozenset({"PUT"}),
        re.compile(r"^/api/agents/[^/]+/artifacts/[^/]+/content$"),
        MAX_ARTIFACT_BYTES + PUT_CONTENT_MARGIN,
    ),
    (
        frozenset({"POST"}),
        re.compile(r"^/api/agents/[^/]+/artifacts/[^/]+/office-asset$"),
        MAX_OFFICE_ASSET_BYTES + MULTIPART_FRAMING_MARGIN,
    ),
    (
        frozenset({"POST"}),
        re.compile(r"^/api/office-watch/edit$"),
        MAX_OFFICE_EDIT_BYTES,
    ),
    (
        frozenset({"POST"}),
        re.compile(r"^/api/public/office-watch-proxy/"),
        MAX_OFFICE_EDIT_BYTES,
    ),
    # env-gated route (registered only under ENABLE_MANYFOLD_API): the
    # pattern sits here unconditionally — with the flag off the path 404s
    # anyway, and a middleware entry for a nonexistent route is inert.
    (
        frozenset({"POST"}),
        re.compile(r"^/v1/chat/completions$"),
        MAX_CHAT_COMPLETIONS_BYTES,
    ),
]


async def body_size_middleware(request: Request, call_next):
    declared = request.headers.get("content-length")
    if declared and declared.isdigit():
        size = int(declared)
        for methods, pattern, cap in BODY_CAPS:
            if request.method in methods and pattern.match(request.url.path):
                if size > cap:
                    return JSONResponse(
                        status_code=413, content={"detail": "request body too large"}
                    )
                break
    return await call_next(request)
