"""
@file_name: test_bundle_preflight_filename.py
@author: Bin Liang
@date: 2026-10-28
@description: The preflight upload is spooled under a FIXED name. Starlette
hands the client's multipart filename through verbatim, so ``../`` or an
absolute path in it used to decide where the bundle bytes were written (and
then unlinked). Revert the fixed name and the traversal case writes outside
the temp dir.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient


@pytest.fixture
def client(monkeypatch, tmp_path):
    import backend.routes.bundle as bundle_mod

    seen = {}

    async def _fake_preflight(path, user_id):
        seen["path"] = path
        return {"ok": True}

    async def _uid(_request):
        return "user_y"

    monkeypatch.setattr(bundle_mod, "preflight", _fake_preflight)
    monkeypatch.setattr(bundle_mod, "_user_id_for_request", _uid)
    monkeypatch.setattr(bundle_mod.tempfile, "mkdtemp", lambda prefix="": str(tmp_path / "spool"))
    (tmp_path / "spool").mkdir()
    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = "user_y"
        return await call_next(request)

    app.include_router(bundle_mod.router, prefix="/api/bundle")
    return TestClient(app), seen, tmp_path


@pytest.mark.parametrize("name", ["../../escape.nxbundle", "/tmp/absolute.nxbundle", "..", "nested/dir/x.nxbundle"])
def test_preflight_spools_under_a_fixed_name(client, name):
    c, seen, tmp_path = client
    r = c.post("/api/bundle/import/preflight", files={"file": (name, b"PK\x05\x06" + b"\0" * 18, "application/zip")})
    assert r.status_code == 200, r.text
    assert seen["path"].name == "upload.nxbundle"
    assert seen["path"].parent == tmp_path / "spool"
