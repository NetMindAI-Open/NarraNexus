"""
@file_name: test_route_registration.py
@author: NarraNexus
@date: 2026-08-13
@description: Every registered route is bound to the handler it looks bound to.

A decorator applies to whatever function follows it. Insert a helper between
`@router.post(...)` and the endpoint it was written for, and the decorator
silently captures the helper: the real handler becomes unreachable and the
helper starts answering HTTP requests with a signature never meant for the
wire.

This branch did exactly that — `_announce_roster(db, team_id, action, agent_id)`
ended up under `@router.post("/{team_id}/members")`, so adding a team member hit
a private helper that takes a database client as a query parameter. Nothing
failed at import, no test covered it, and the frontend would have surfaced it as
"adding a member is broken" with no clue why.

The guard is a naming convention rather than a per-route allowlist: a private
helper starts with `_`, an endpoint does not. That makes the check apply to every
router in the app, including ones written after this file, and it costs nothing
to keep true.
"""

from __future__ import annotations

from fastapi.routing import APIRoute


def _api_routes():
    from backend.main import app

    return [r for r in app.routes if isinstance(r, APIRoute)]


def test_no_route_is_bound_to_a_private_helper():
    """`_`-prefixed names are helpers by this codebase's convention, so one
    answering HTTP means a decorator captured the wrong function."""
    offenders = [
        f"{sorted(r.methods)} {r.path} -> {r.endpoint.__name__}"
        for r in _api_routes()
        if r.endpoint.__name__.startswith("_")
    ]

    assert offenders == [], (
        "these routes are bound to private helpers, which happens when a "
        f"function is inserted between a decorator and its endpoint: {offenders}"
    )


def test_adding_a_team_member_is_bound_to_add_member():
    """The specific regression, named so the failure says what broke."""
    bound = {
        (r.path, m, r.endpoint.__name__) for r in _api_routes() for m in r.methods
    }

    assert ("/api/teams/{team_id}/members", "POST", "add_member") in bound


def test_no_two_routes_claim_the_same_method_and_path():
    """The other half of the same accident: a decorator copied onto a second
    function leaves the first one shadowed, and FastAPI resolves whichever
    registered first with no warning."""
    seen: dict[tuple[str, str], str] = {}
    clashes = []
    for r in _api_routes():
        for method in r.methods:
            key = (r.path, method)
            if key in seen:
                clashes.append(f"{method} {r.path}: {seen[key]} and {r.endpoint.__name__}")
            else:
                seen[key] = r.endpoint.__name__

    assert clashes == [], f"duplicate route registrations: {clashes}"
