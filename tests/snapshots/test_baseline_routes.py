"""
@file_name: test_baseline_routes.py
@author: Bin Liang
@date: 2026-09-03
@description: Pin every (method, path, endpoint) the backend app registers, for the production shape.

Baseline for the plugin-platform refactor: route registration moves from
hand-written ``include_router`` lines to a registry, and this snapshot is the
proof that the set of routes did not change while that happens.

The app is imported in a fresh interpreter with a built frontend present
(``FRONTEND_DIST`` pointing at a directory with an ``index.html``), because
that is the shape the desktop bundle and the cloud image serve — the SPA
fallback routes are exactly the ones a route-registry refactor is most likely
to lose. The Manyfold-gated routers are pinned separately.
"""
from __future__ import annotations

from tests.snapshots._approval import approve
from tests.snapshots._subprocess import run_probe

_PROBE = """
import json
from fastapi.routing import APIRoute
from backend.main import app
rows = sorted(
    [method, route.path, route.endpoint.__name__]
    for route in app.routes
    if isinstance(route, APIRoute)
    for method in route.methods
)
print(json.dumps(rows))
"""


def _routes(tmp_path, *, manyfold: bool, frontend: bool = True) -> list[list[str]]:
    dist = tmp_path / ("dist" if frontend else "no-dist")
    if frontend:
        (dist / "assets").mkdir(parents=True, exist_ok=True)  # main.py mounts dist/assets
        (dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    env = {"NARRANEXUS_DEPLOYMENT_MODE": "local", "FRONTEND_DIST": str(dist)}
    if manyfold:
        env["ENABLE_MANYFOLD_API"] = "1"
    return run_probe(_PROBE, env=env)


def test_registered_routes_are_unchanged(tmp_path):
    rows = _routes(tmp_path, manyfold=False)
    assert ["GET", "/{full_path:path}", "spa_fallback"] in rows, "the SPA fallback must be part of the pinned shape"
    approve("routes", rows)


def test_api_only_shape_keeps_the_root_endpoint(tmp_path):
    """Without a built frontend the app serves a JSON root instead of the SPA fallback."""
    rows = _routes(tmp_path, manyfold=False, frontend=False)
    assert ["GET", "/", "root"] in rows
    approve("routes_api_only", rows)


def test_manyfold_gated_routes_are_unchanged(tmp_path):
    base = {tuple(r) for r in _routes(tmp_path, manyfold=False)}
    with_manyfold = _routes(tmp_path, manyfold=True)
    extra = sorted(list(r) for r in with_manyfold if tuple(r) not in base)
    assert extra, "ENABLE_MANYFOLD_API=1 must add routes"
    approve("routes_manyfold_extra", extra)
