"""
@file_name: test_spa_fallback_404.py
@author: Bin Liang
@date: 2026-09-07
@description: `f4b66b385` widened `spa_fallback`'s 404 prefix set from
             ("v1/", "manyfold/") to ("v1/", "manyfold/", "api/", "ws/") — an
             unmatched /api/* or /ws/* request must now 404 instead of
             falling through to index.html+200.

`_FRONTEND_DIST` and `spa_fallback` are evaluated at MODULE IMPORT TIME
(`backend/main.py` only registers the route inside `if _FRONTEND_DIST.is_dir()
and (_FRONTEND_DIST / "index.html").exists():`). A plain TestClient run in the
current interpreter either reuses whatever `backend.main` some earlier test
already imported, or imports it with no built frontend present — either way
`spa_fallback` is never registered and every "404" in that shape actually
comes from FastAPI's default 404, not from the prefix check this test exists
to protect. Reverting the whole fix would pass a TestClient-only test.

So this runs in a subprocess (like `tests/snapshots/test_baseline_routes.py`)
with `FRONTEND_DIST` pointing at a directory that has an `index.html`, which
is the only shape where `spa_fallback` is actually wired in.
"""
from __future__ import annotations

from tests.snapshots._subprocess import run_probe

_PROBE = """
import json
from fastapi.testclient import TestClient
from backend.main import app

client = TestClient(app)
# /api/* in local mode requires X-User-Id (see backend/auth.py auth_middleware)
# or auth_middleware 401s before the request ever reaches spa_fallback — which
# would make an unauthenticated /api/nope probe test the auth layer, not the
# 404-prefix fix this file exists to guard.
paths = ["/api/nope", "/ws/nope", "/v1/nope", "/manyfold/nope", "/some/spa/route"]
results = {}
for path in paths:
    resp = client.get(path, headers={"X-User-Id": "local-default"})
    try:
        body = resp.json()
        is_json = True
    except ValueError:
        body = resp.text
        is_json = False
    results[path] = {"status": resp.status_code, "is_json": is_json, "body": body}
print(json.dumps(results))
"""


def _probe(tmp_path) -> dict:
    dist = tmp_path / "dist"
    (dist / "assets").mkdir(parents=True, exist_ok=True)
    (dist / "index.html").write_text("<!doctype html><html>spa shell</html>", encoding="utf-8")
    env = {"NARRANEXUS_DEPLOYMENT_MODE": "local", "FRONTEND_DIST": str(dist)}
    return run_probe(_PROBE, env=env)


def test_unmatched_api_path_is_a_404_json_response(tmp_path):
    result = _probe(tmp_path)["/api/nope"]
    assert result["status"] == 404
    assert result["is_json"], "an unregistered /api/* path must not fall through to the SPA HTML shell"


def test_unmatched_ws_path_is_a_404_json_response(tmp_path):
    result = _probe(tmp_path)["/ws/nope"]
    assert result["status"] == 404
    assert result["is_json"]


def test_unmatched_v1_path_is_still_a_404_json_response(tmp_path):
    """Pre-existing prefix (not part of this fix) — must not regress."""
    result = _probe(tmp_path)["/v1/nope"]
    assert result["status"] == 404
    assert result["is_json"]


def test_unmatched_manyfold_path_is_still_a_404_json_response(tmp_path):
    """Pre-existing prefix (not part of this fix) — must not regress."""
    result = _probe(tmp_path)["/manyfold/nope"]
    assert result["status"] == 404
    assert result["is_json"]


def test_a_real_spa_route_still_gets_the_html_shell(tmp_path):
    """The allow-list case: fixing the 404 prefixes must not fail-closed the
    entire SPA fallback and 404 legitimate client-side routes too."""
    result = _probe(tmp_path)["/some/spa/route"]
    assert result["status"] == 200
    assert not result["is_json"]
    assert "spa shell" in result["body"]
