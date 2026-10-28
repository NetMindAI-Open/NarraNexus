"""
@file_name: test_agents_files_traversal.py
@author: Bin Liang
@date: 2026-10-28
@description: Regression tests for the 2026-10-28 cross-tenant workspace
read. ``/api/agents/{agent_id}/files*`` used to splice the URL's
``agent_id`` straight into ``{base}/{user_id}/{agent_id}``; ``agent_id=..``
resolved to the workspace ROOT and the path checks (which only looked at
``?path=``) happily served every other user's files. These tests pin:

- a dot-led / slash-bearing ``agent_id`` is rejected before any disk access,
- an agent the caller does not own is rejected (404, no existence oracle),
- the owner's own listing / raw / delete still work,
- nothing outside the caller's workspace is ever listed, read or deleted.

The attachments router shares the same gate and gets the same cases.
Neuter ``require_owned_workspace_agent`` in ``backend/routes/_ownership.py`` and these go red.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from narranexus.platform.utils.workspace_paths import agent_workspace_relpath

OWNER = "user_owner_000000000000000000000"
VICTIM = "user_victim_00000000000000000000"
OWNED_AGENT = "agent_0123456789ab"
VICTIM_AGENT = "agent_ba9876543210"


async def _async_return(value):
    return value


@pytest.fixture
async def setup(db_client, monkeypatch, tmp_path):
    base = tmp_path / "workspaces"
    own_ws = base / agent_workspace_relpath(OWNED_AGENT, OWNER)
    victim_ws = base / agent_workspace_relpath(VICTIM_AGENT, VICTIM)
    own_ws.mkdir(parents=True)
    victim_ws.mkdir(parents=True)
    (own_ws / "mine.txt").write_text("mine", encoding="utf-8")
    secret = victim_ws / "credentials.json"
    secret.write_text('{"api_key": "victim-secret"}', encoding="utf-8")

    from narranexus.platform.settings import settings as sa_settings
    monkeypatch.setattr(sa_settings, "base_working_path", str(base), raising=False)

    import backend.routes.agents.files as files_mod
    import backend.routes.agents.attachments as att_mod
    import backend.routes._ownership as own_mod
    from narranexus_plugins.skill_module import routes as skills_mod
    monkeypatch.setattr(own_mod, "get_db_client", lambda: _async_return(db_client))
    # The skills router reaches the same gate through the SDK seam.
    from narranexus.contracts.services import WEB_HOST
    from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
    from backend.plugin_sdk_host import HOST_OWNER, install_web_host
    had_host = KERNEL_REGISTRIES.services.try_require(WEB_HOST) is not None
    install_web_host()

    await db_client.insert("agents", {
        "agent_id": OWNED_AGENT, "agent_name": "mine", "created_by": OWNER,
    })
    await db_client.insert("agents", {
        "agent_id": VICTIM_AGENT, "agent_name": "theirs", "created_by": VICTIM,
    })

    app = FastAPI()

    @app.middleware("http")
    async def _identity(request: Request, call_next):
        request.state.user_id = request.headers.get("X-User-Id") or None
        return await call_next(request)

    app.include_router(files_mod.router, prefix="/api/agents")
    app.include_router(att_mod.router, prefix="/api/agents")
    app.include_router(skills_mod.router, prefix="/api/skills")
    yield {
        "client": TestClient(app),
        "base": base,
        "secret": secret,
        "victim_ws": victim_ws,
    }
    if not had_host:
        KERNEL_REGISTRIES.services.release_owner(HOST_OWNER)


AS_OWNER = {"X-User-Id": OWNER}
VICTIM_REL = f"{VICTIM}/{VICTIM_AGENT}/credentials.json"


# --- traversal via the agent_id segment ------------------------------------

# NOTE: httpx collapses a literal ".." / "." segment and decodes %2f before the
# request is sent, so those spellings never reach the app and would pass for
# the wrong reason. Only percent-encoded dots and the other dot-led variants
# arrive intact — the attacker's "%2e%2e" is among them.
DOT_AGENT_IDS = ["%2e%2e", "%2E%2E", "...", "..;", ".hidden", "..%5cx"]


@pytest.mark.parametrize("bad_agent", DOT_AGENT_IDS)
def test_dot_or_slash_agent_id_never_lists_root(setup, bad_agent):
    client = setup["client"]
    r = client.get(f"/api/agents/{bad_agent}/files", headers=AS_OWNER)
    assert r.status_code == 404, r.text
    assert VICTIM not in r.text
    assert "tree" not in r.json()


def test_dotdot_agent_id_cannot_read_other_users_file(setup):
    client = setup["client"]
    for bad in DOT_AGENT_IDS:
        r = client.get(
            f"/api/agents/{bad}/files/raw", params={"path": VICTIM_REL}, headers=AS_OWNER
        )
        assert r.status_code == 404, r.text
        assert "victim-secret" not in r.text


def test_dotdot_agent_id_cannot_delete_other_users_dir(setup):
    client = setup["client"]
    for bad in DOT_AGENT_IDS:
        r = client.request(
            "DELETE", f"/api/agents/{bad}/files/{VICTIM}/{VICTIM_AGENT}", headers=AS_OWNER
        )
        assert r.status_code == 404, (bad, r.text)
    assert setup["secret"].exists()
    assert setup["victim_ws"].is_dir()


# --- ownership --------------------------------------------------------------

def test_well_formed_but_foreign_agent_id_is_404(setup):
    client = setup["client"]
    r = client.get(f"/api/agents/{VICTIM_AGENT}/files", headers=AS_OWNER)
    assert r.status_code == 404
    r = client.get(
        f"/api/agents/{VICTIM_AGENT}/files/raw", params={"path": "credentials.json"},
        headers=AS_OWNER,
    )
    assert r.status_code == 404
    assert "victim-secret" not in r.text
    r = client.request(
        "DELETE", f"/api/agents/{VICTIM_AGENT}/files/credentials.json", headers=AS_OWNER
    )
    assert r.status_code == 404
    assert setup["secret"].exists()


def test_unknown_agent_id_is_404_not_500(setup):
    client = setup["client"]
    r = client.get("/api/agents/agent_doesnotexist/files", headers=AS_OWNER)
    assert r.status_code == 404


def test_no_identity_is_401(setup):
    client = setup["client"]
    r = client.get(f"/api/agents/{OWNED_AGENT}/files")
    assert r.status_code == 401


# --- the owner's own workspace still works ---------------------------------

def test_owner_can_list_read_upload_delete(setup):
    client = setup["client"]
    r = client.get(f"/api/agents/{OWNED_AGENT}/files", headers=AS_OWNER)
    assert r.status_code == 200, r.text
    names = [n["name"] for n in r.json()["tree"]]
    assert names == ["mine.txt"]
    assert VICTIM not in r.text

    r = client.get(
        f"/api/agents/{OWNED_AGENT}/files/raw", params={"path": "mine.txt"}, headers=AS_OWNER
    )
    assert r.status_code == 200
    assert r.text == "mine"

    r = client.post(
        f"/api/agents/{OWNED_AGENT}/files",
        files={"file": ("up.txt", b"hello", "text/plain")},
        headers=AS_OWNER,
    )
    assert r.status_code == 200 and r.json()["success"] is True

    r = client.request("DELETE", f"/api/agents/{OWNED_AGENT}/files/up.txt", headers=AS_OWNER)
    assert r.status_code == 200 and r.json()["success"] is True


def test_path_param_traversal_inside_owned_agent_still_rejected(setup):
    client = setup["client"]
    r = client.get(
        f"/api/agents/{OWNED_AGENT}/files/raw",
        params={"path": f"../../{VICTIM_REL}"},
        headers=AS_OWNER,
    )
    assert r.status_code == 400
    assert "victim-secret" not in r.text


# --- attachments share the same gate ----------------------------------------

@pytest.mark.parametrize("bad_agent", DOT_AGENT_IDS + [VICTIM_AGENT])
def test_attachment_upload_rejects_foreign_or_dot_agent(setup, bad_agent):
    client = setup["client"]
    r = client.post(
        f"/api/agents/{bad_agent}/attachments",
        files={"file": ("x.txt", b"hi", "text/plain")},
        headers=AS_OWNER,
    )
    assert r.status_code == 404, r.text
    # Nothing landed anywhere under the base — not in the root, not in the
    # victim's tree (attachments live under <workspace>/user_upload_files).
    assert not list(setup["base"].glob("**/user_upload_files"))


def _seed_victim_attachment(setup) -> str:
    """Store a real attachment in the VICTIM's workspace and return its file_id."""
    from narranexus.platform.utils.attachment_storage import store_uploaded_attachment
    file_id, _ = store_uploaded_attachment(
        VICTIM_AGENT, VICTIM, raw_bytes=b"victim-secret-attachment",
        original_name="s.txt", mime_type="text/plain",
    )
    return file_id


@pytest.mark.parametrize("bad_agent", DOT_AGENT_IDS + [VICTIM_AGENT])
def test_attachment_raw_rejects_foreign_or_dot_agent(setup, bad_agent):
    """A never-leaks check rather than a regression pin: the attachment path
    is always built under the CALLER's user dir, so even the pre-fix code
    could not serve the victim's file here. What the gate changes is the
    answer — a clean 404 instead of a ValueError-driven 500."""
    client = setup["client"]
    file_id = _seed_victim_attachment(setup)
    r = client.get(f"/api/agents/{bad_agent}/attachments/{file_id}/raw", headers=AS_OWNER)
    assert r.status_code == 404
    assert "victim-secret" not in r.text


def test_attachment_upload_then_raw_for_owner(setup):
    client = setup["client"]
    r = client.post(
        f"/api/agents/{OWNED_AGENT}/attachments",
        files={"file": ("x.txt", b"hi", "text/plain")},
        headers=AS_OWNER,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True, body
    r = client.get(f"/api/agents/{OWNED_AGENT}/attachments/{body['file_id']}/raw", headers=AS_OWNER)
    assert r.status_code == 200 and r.content == b"hi"


# --- skills routes take agent_id from the query / form: same gate ----------

@pytest.mark.parametrize("bad_agent", ["..", f"../{VICTIM}/{VICTIM_AGENT}", "..\\x", ".x", "", VICTIM_AGENT])
def test_skills_routes_reject_foreign_or_dot_agent(setup, bad_agent):
    # Query values arrive decoded, so a literal ".." reaches the handler here
    # (unlike a path segment, which httpx would normalise away).
    client = setup["client"]
    r = client.get("/api/skills", params={"agent_id": bad_agent}, headers=AS_OWNER)
    assert r.status_code == 404, r.text
    r = client.get("/api/skills/arena", params={"agent_id": bad_agent}, headers=AS_OWNER)
    assert r.status_code == 404, r.text
    r = client.request("DELETE", "/api/skills/arena", params={"agent_id": bad_agent}, headers=AS_OWNER)
    assert r.status_code == 404, r.text
    r = client.put("/api/skills/arena/env", params={"agent_id": bad_agent}, json={"env": {}}, headers=AS_OWNER)
    assert r.status_code == 404, r.text


def test_skills_list_for_owner_still_works(setup):
    client = setup["client"]
    r = client.get("/api/skills", params={"agent_id": OWNED_AGENT}, headers=AS_OWNER)
    assert r.status_code == 200, r.text
    assert isinstance(r.json().get("skills"), list)


@pytest.mark.parametrize("bad_name", ["%2e%2e", "...", ".disabled", "..%5cx"])
def test_skills_routes_reject_unsafe_skill_name(setup, bad_name):
    client = setup["client"]
    for method, path, kw in (
        ("GET", f"/api/skills/{bad_name}", {}),
        ("DELETE", f"/api/skills/{bad_name}", {}),
        ("PUT", f"/api/skills/{bad_name}/disable", {}),
        ("PUT", f"/api/skills/{bad_name}/enable", {}),
        ("GET", f"/api/skills/{bad_name}/env", {}),
        ("PUT", f"/api/skills/{bad_name}/env", {"json": {"env": {}}}),
        ("POST", f"/api/skills/{bad_name}/study", {"json": {}}),
        ("GET", f"/api/skills/{bad_name}/study", {}),
    ):
        r = client.request(method, path, params={"agent_id": OWNED_AGENT}, headers=AS_OWNER, **kw)
        assert r.status_code == 404, (method, path, r.text)
    assert (setup["base"] / f"{OWNER}/{OWNED_AGENT}/mine.txt").exists()


# --- a swapped workspace dir (symlink) is refused -------------------------------

def test_symlinked_workspace_dir_serves_nothing(setup):
    import os
    import shutil
    base = setup["base"]
    own_ws = base / OWNER / OWNED_AGENT
    shutil.rmtree(own_ws)
    os.symlink(base / VICTIM / VICTIM_AGENT, own_ws)
    client = setup["client"]
    r = client.get(f"/api/agents/{OWNED_AGENT}/files", headers=AS_OWNER)
    assert r.status_code == 404, r.text
    assert "credentials" not in r.text
    r = client.get(f"/api/agents/{OWNED_AGENT}/files/raw", params={"path": "credentials.json"}, headers=AS_OWNER)
    assert r.status_code == 404
    assert "victim-secret" not in r.text
    r = client.request("DELETE", f"/api/agents/{OWNED_AGENT}/files/credentials.json", headers=AS_OWNER)
    assert r.status_code == 404
    assert setup["secret"].exists()


# --- the tree has a ceiling -------------------------------------------------------

def test_tree_listing_is_capped_and_says_so(setup, monkeypatch):
    import backend.routes.agents.files as files_mod
    monkeypatch.setattr(files_mod, "MAX_TREE_NODES", 10)
    monkeypatch.setattr(files_mod, "MAX_TREE_DEPTH", 3)
    ws = setup["base"] / OWNER / OWNED_AGENT
    for i in range(30):
        (ws / f"f{i:02d}.txt").write_text("x", encoding="utf-8")
    deep = ws / "d1" / "d2" / "d3" / "d4"
    deep.mkdir(parents=True)
    (deep / "leaf.txt").write_text("x", encoding="utf-8")
    r = setup["client"].get(f"/api/agents/{OWNED_AGENT}/files", headers=AS_OWNER)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["truncated"] is True

    def _count(nodes):
        return sum(1 + _count(n.get("children") or []) for n in nodes)
    assert _count(body["tree"]) <= 10
    assert "leaf.txt" not in r.text


def test_depth_cap_on_an_empty_directory_is_not_reported_as_truncated(setup, monkeypatch):
    """Reaching the depth ceiling only truncates when there is something below
    it; an empty folder at the last level is a complete listing."""
    import backend.routes.agents.files as files_mod
    monkeypatch.setattr(files_mod, "MAX_TREE_DEPTH", 3)
    ws = setup["base"] / OWNER / OWNED_AGENT
    (ws / "d1" / "d2" / "d3").mkdir(parents=True)
    (ws / "d1" / "d2" / "d3" / ".hidden").write_text("x", encoding="utf-8")
    r = setup["client"].get(f"/api/agents/{OWNED_AGENT}/files", headers=AS_OWNER)
    assert r.status_code == 200, r.text
    assert r.json()["truncated"] is False

    (ws / "d1" / "d2" / "d3" / "below.txt").write_text("x", encoding="utf-8")
    r = setup["client"].get(f"/api/agents/{OWNED_AGENT}/files", headers=AS_OWNER)
    assert r.json()["truncated"] is True
    assert "below.txt" not in r.text
