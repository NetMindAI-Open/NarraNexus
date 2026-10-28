"""
@file_name: _mcp_headers.py
@author: NarraNexus
@date: 2026-08-14
@description: Fake an ambient MCP request so identity-header code runs for real.

Every tool that reads its caller's identity does so from the ambient
``request_ctx`` — agent id, turn source, errand scope, team, event id. Testing
any of them means standing up that context, and the alternative (asserting on
`_mcp_identity`'s internals) tests the parser rather than the wiring, which is
the half that actually breaks.

Extracted from `test_bus_send_stamp.py` when `test_bus_send_event_id_stamp.py`
became the second copy. Not a helper for its own sake: the fake request's shape
tracks the `mcp` library, so a second copy means a second place to fix when
that shape changes.
"""
from __future__ import annotations

import contextlib


class _Headers(dict):
    """Case-insensitive `get`, because `_explicit_header` looks up lowercase.

    Deliberately not a plain dict: real transports preserve the sender's
    casing, and a test that only works with pre-lowercased keys would pass
    while production missed the header.
    """

    def get(self, key, default=None):  # noqa: D102
        return super().get(key.lower(), default)


@contextlib.contextmanager
def injected(headers: dict):
    """Run the block as if an MCP tool call arrived carrying ``headers``."""
    from mcp.server.lowlevel.server import request_ctx

    request = type("Req", (), {
        "headers": _Headers({k.lower(): v for k, v in headers.items()})
    })()
    token = request_ctx.set(type("Ctx", (), {"request": request})())
    try:
        yield
    finally:
        # In `finally`, always: one failing test leaking this context would
        # silently change the caller identity of every test after it.
        request_ctx.reset(token)
