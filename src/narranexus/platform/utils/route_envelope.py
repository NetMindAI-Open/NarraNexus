"""
@file_name: route_envelope.py
@author: Bin Liang
@date: 2026-09-09
@description: ``structured_envelope`` — the outer fallback for routes whose contract is ``{"success": False, "error": ...}`` at 200.

A family of routes (the generic ``/api/channels/{channel}/…`` verbs, the Lark
OAuth flow) answers EVERY expected failure — ownership denied, nothing bound,
a validation miss — as a JSON envelope at HTTP 200, and the frontend branches
on ``success``. What none of them had was a fallback for the UNEXPECTED
exception: a driver error, a subprocess that died, the next bug in a parser.
Those propagated out of the route and Starlette's default error middleware
turned them into a plain-text ``"Internal Server Error"`` the frontend cannot
read an ``error`` field out of (GitHub #118 residue).

One decorator, not six copies of the same ``try/except`` (B-31 review round 1
found the first copy applied to 1 of 7 sibling routes). Two rules it enforces
that a hand-written copy is prone to forget:

1. **``HTTPException`` keeps its status code.** ``check_owned`` deliberately
   raises 503 when the ownership lookup itself fails so a db outage produces
   a 5xx the access-log middleware can alarm on; ``CredentialConflict`` maps
   to 409, an unknown channel to 404, and the host's ``AuthError`` IS an
   ``HTTPException``. A bare ``except Exception`` would downgrade all of them
   to a 200 envelope and erase the alarm signal (``_ownership.py``'s "no 5xx
   metric to alarm on" failure — PR #258 review #4). Typed rejections are
   re-raised first.
2. **The client never sees ``str(e)``.** A driver's exception text carries
   hostnames, SQL fragments and container paths. The response carries a fixed
   sentence plus a trace id; the full exception goes to the server log under
   that same id (``logger.exception``), so support can join the two.

Not for routes whose status code IS the contract (the anonymous inbound
webhook answers 401/404/429 by status — wrapping it would make a rejected
delivery look accepted). ``CancelledError`` is a ``BaseException`` and passes
through untouched, so a client disconnect still unwinds the handler.
"""
from __future__ import annotations

import functools
import inspect
import typing
from typing import Any, Awaitable, Callable, TypeVar
from uuid import uuid4

from fastapi import HTTPException
from loguru import logger

#: The one sentence a client sees for an unexpected failure. The trace id is
#: appended so a user can quote it; the response also carries it as its own
#: field so a UI can render it without parsing prose.
UNEXPECTED_ERROR_TEXT = "Unexpected server error"

F = TypeVar("F", bound=Callable[..., Awaitable[dict[str, Any]]])


def new_trace_id() -> str:
    """A short id that joins one response to one ``logger.exception`` line."""
    return f"err_{uuid4().hex[:8]}"


def _resolved_signature(fn: Callable[..., Any]) -> inspect.Signature:
    """``fn``'s signature with string annotations resolved in ``fn``'s own module.

    FastAPI reads a route's parameters through ``inspect.signature`` and
    evaluates string annotations (``from __future__ import annotations``)
    against the ENDPOINT's ``__globals__``. A wrapper's globals are this
    module's, where ``Request`` / the route's body models do not exist, so
    without this the decorated route would fail to build. Resolving here,
    with ``typing.get_type_hints`` (which follows ``__wrapped__`` to the
    right namespace), and pinning the result as ``__signature__`` hands
    FastAPI real types instead of forward references.
    """
    hints = typing.get_type_hints(fn)
    sig = inspect.signature(fn)
    params = [
        p.replace(annotation=hints.get(p.name, p.annotation))
        for p in sig.parameters.values()
    ]
    return sig.replace(
        parameters=params,
        return_annotation=hints.get("return", sig.return_annotation),
    )


def structured_envelope(scope: str) -> Callable[[F], F]:
    """Wrap an async route so an unexpected exception returns the route's own envelope.

    Args:
        scope: The log prefix (``"channels"``, ``"lark"``) — the same tag the
            route family already logs under, so the trace id lands next to
            its neighbours in the access log.
    """

    def decorate(fn: F) -> F:
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> dict[str, Any]:
            try:
                return await fn(*args, **kwargs)
            except HTTPException:
                # Typed rejections (404 unknown channel, 409 conflict, 503
                # ownership lookup failed, 401/403 AuthError) keep their
                # status code — only truly unexpected exceptions fall through.
                raise
            except Exception as e:  # noqa: BLE001 — the outer fallback IS the point
                trace_id = new_trace_id()
                logger.exception(f"[{scope}] {fn.__name__} crashed (trace_id={trace_id}): {e}")
                return {
                    "success": False,
                    "error": f"{UNEXPECTED_ERROR_TEXT} (ref {trace_id})",
                    "trace_id": trace_id,
                }

        wrapper.__signature__ = _resolved_signature(fn)  # type: ignore[attr-defined]
        return wrapper  # type: ignore[return-value]

    return decorate


__all__ = ["UNEXPECTED_ERROR_TEXT", "new_trace_id", "structured_envelope"]
