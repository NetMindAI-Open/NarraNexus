"""
@file_name: test_builtin_routes_ownership.py
@author: Bin Liang
@date: 2026-09-04
@description: Every feature router the platform used to include by hand is a backend.routes contribution of one builtin; disabling that builtin 404s its paths and leaves the others mounted.
"""
from __future__ import annotations

import inspect
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from narranexus.hosts.boot import boot
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.builtins import load_builtins

OWNED = {
    "builtin.teams": {"teams", "marketplace_teams"},
    "builtin.awareness": {"agents_awareness", "agents_profile"},
    "builtin.social_network": {"agents_social_network"},
    "builtin.basic_info": {"agents_narrative"},
    "builtin.chat": {"agents_chat_history"},
    "builtin.job": {"agents_jobs", "jobs", "dashboard_jobs"},
    "builtin.skills": {"skills"},
    "builtin.home_assistant": {"home_assistant"},
    # Only the channels with a channel-SPECIFIC flow keep a router (Lark OAuth,
    # WeChat QR, NarraMessenger prewarm); slack/telegram/discord bind through the
    # shell-level generic /api/channels router since batch 4d.3 and own no routes.
    **{f"builtin.channels.{ch}": {f"channel_{ch}"} for ch in ("lark", "wechat", "narramessenger")},
}


def _boot(tmp_path: Path, monkeypatch, disable: str | None = None) -> Registries:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(home))
    store = RegistryStore(path=home / "registry.json", lkg=home / "lkg.json")
    if disable:
        store.update(lambda reg: reg.builtin_overrides.__setitem__(disable, {"enabled": False}))
    regs = Registries()
    load_builtins(regs, "backend")
    boot("backend", registries=regs, cloud=False, host_version="1.19.0", store=store)
    return regs


def test_every_feature_router_is_owned_by_exactly_one_builtin(tmp_path, monkeypatch):
    regs = _boot(tmp_path, monkeypatch)
    by_owner: dict[str, set[str]] = {}
    for e in regs.registry_for("backend.routes").entries():
        by_owner.setdefault(e.owner, set()).add(e.name)
    assert by_owner == OWNED


@pytest.mark.parametrize("plugin_id,path", [("builtin.channels.lark", "/api/lark/"), ("builtin.skills", "/api/skills/"), ("builtin.home_assistant", "/api/home-assistant/")])
def test_disabling_a_builtin_removes_its_router_only(plugin_id, path, tmp_path, monkeypatch):
    from backend.plugins_host import mount_plugin_routes

    regs = _boot(tmp_path, monkeypatch, disable=plugin_id)
    app = FastAPI()
    report = mount_plugin_routes(app, regs)
    assert all(owner != plugin_id for owner, _, _ in report.mounted)
    assert len(report.mounted) == sum(len(v) for v in OWNED.values()) - len(OWNED[plugin_id])
    client = TestClient(app)
    assert client.get(path).status_code == 404
    # builtin.job's router is still mounted: the request reaches the route
    # handler (422 for a missing required query param), not FastAPI's
    # catch-all 404 for an unmatched path — which a 500 or any other
    # non-404 status would also satisfy under "!= 404".
    assert client.get("/api/jobs/").status_code == 422


def test_disabling_builtin_job_removes_dashboard_controls_but_not_dashboard_reads(tmp_path, monkeypatch):
    from backend.plugins_host import mount_plugin_routes

    regs = _boot(tmp_path, monkeypatch, disable="builtin.job")
    app = FastAPI()
    mount_plugin_routes(app, regs)
    paths = {getattr(r, "path", "") for r in app.routes}
    assert "/api/dashboard/jobs/{job_id}/pause" not in paths and "/api/jobs/" not in {p.rstrip("/") + "/" for p in paths}
    import backend.routes.dashboard.routes as dash

    src = inspect.getsource(dash)
    assert "/jobs/{job_id}/retry" in src and "/jobs/{job_id}/pause" not in src  # retry stays platform, pause moved


def test_main_no_longer_includes_the_moved_routers():
    main = Path(__file__).resolve().parents[2] / "backend" / "main.py"
    src = main.read_text()
    for name in ("jobs_router", "skills_router", "home_assistant_router", "lark_router", "slack_router", "telegram_router", "wechat_router", "narramessenger_router", "discord_router", "marketplace_teams_router"):
        assert f"include_router({name}" not in src, name
