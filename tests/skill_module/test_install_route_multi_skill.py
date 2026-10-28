"""
@file_name: test_install_route_multi_skill.py
@author: Bin Liang
@date: 2026-09-10
@description: POST /api/skills/install at the ROUTE level for multi-skill
GitHub repos. The pipeline layer already isolates per-skill failures; this
pins the HTTP verdict: partial success -> 200 with one line per skill,
zero success -> 400 carrying every failure line (a 4xx raised inside the
handler's try used to be re-labelled 500 by its `except Exception`).
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus.platform.marketplace._skill_marketplace_impl.install_pipeline import InstallResult
from narranexus.platform.schema.skill_schema import SkillInfo
from narranexus_plugins.skill_module import routes as skills_routes


def _client(monkeypatch, results):
    class _FakePipeline:
        def __init__(self, *_a, **_k):
            pass

        async def install_from_github(self, url, branch="main"):
            return results

    async def _user(_request, _agent_id):
        return "u1"

    monkeypatch.setattr(skills_routes, "require_workspace_agent_owner", _user)
    monkeypatch.setattr(skills_routes, "_get_skill_module", lambda agent_id, user_id: object())
    import narranexus.platform.marketplace as marketplace

    monkeypatch.setattr(marketplace, "InstallPipeline", _FakePipeline)
    app = FastAPI()
    app.include_router(skills_routes.router, prefix="/api/skills")
    return TestClient(app)


def _post(client):
    return client.post(
        "/api/skills/install",
        data={"agent_id": "a1", "source": "github", "url": "https://github.com/acme/mixed"},
    )


OK_ALPHA = InstallResult(status="installed", skill=SkillInfo(name="alpha", description="", path="/x/alpha"))
BAD_EVIL = InstallResult(status="failed", skill=None, skill_name="evil", error="Security scan rejected this skill package (curl_pipe_sh)")
BAD_BETA = InstallResult(status="failed", skill=None, skill_name="beta", error="Missing skill dependencies: gamma")


def test_all_roots_rejected_is_a_400_listing_every_failure(monkeypatch):
    resp = _post(_client(monkeypatch, [BAD_EVIL, BAD_BETA]))

    assert resp.status_code == 400
    detail = resp.json()["detail"]
    assert "Skill 'evil' was NOT installed: Security scan rejected" in detail
    assert "Skill 'beta' was NOT installed: Missing skill dependencies" in detail
    assert not detail.startswith("400:")  # not a re-labelled HTTPException


def test_partial_success_is_a_200_with_both_verdicts(monkeypatch):
    resp = _post(_client(monkeypatch, [OK_ALPHA, BAD_EVIL]))

    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    assert body["skill"]["name"] == "alpha"
    assert "Skill 'alpha' installed successfully" in body["message"]
    assert "Skill 'evil' was NOT installed" in body["message"]


def test_repo_level_value_error_is_still_a_400(monkeypatch):
    class _Boom:
        def __init__(self, *_a, **_k):
            pass

        async def install_from_github(self, url, branch="main"):
            raise ValueError("Invalid skill: no SKILL.md found in https://github.com/acme/mixed (branch main)")

    async def _user(_request, _agent_id):
        return "u1"

    monkeypatch.setattr(skills_routes, "require_workspace_agent_owner", _user)
    monkeypatch.setattr(skills_routes, "_get_skill_module", lambda agent_id, user_id: object())
    import narranexus.platform.marketplace as marketplace

    monkeypatch.setattr(marketplace, "InstallPipeline", _Boom)
    app = FastAPI()
    app.include_router(skills_routes.router, prefix="/api/skills")
    resp = _post(TestClient(app))
    assert resp.status_code == 400
    assert "no SKILL.md found" in resp.json()["detail"]


@pytest.mark.parametrize("source", ["ftp", "github"])
def test_bad_form_is_rejected_before_the_pipeline(monkeypatch, source):
    client = _client(monkeypatch, [OK_ALPHA])
    resp = client.post("/api/skills/install", data={"agent_id": "a1", "source": source})
    assert resp.status_code == 400
