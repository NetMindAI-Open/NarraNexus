"""
@file_name: test_flow.py
@author: Bin Liang
@date: 2026-09-03
@description: The whole self-extension flow: scaffold → edit → validate → test → register (green report required) → activate proposal → user approves → observe → deactivate → rollback twice → manual.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from narranexus_plugins.nexus_plugins_module._nexus_plugins_impl.guards import GuardError


def test_happy_path(env, svc):
    out = svc.scaffold("me.weather", ["routes", "settings"], "Weather")
    dest = Path(out["path"])
    assert dest == env["workspace"] / "plugins" / "me.weather" and (dest / "narranexus-plugin.json").is_file()
    with pytest.raises(GuardError, match="already exists"):
        svc.scaffold("me.weather", ["routes"])

    edit = svc.edit("me.weather", "backend/notes.md", "# notes\n", why="docs")
    assert edit["bytes"] == 8 and (dest / ".plugin-changelog.jsonl").is_file()
    with pytest.raises(GuardError):
        svc.edit("me.weather", "../escape.py", "x")

    v = svc.validate("me.weather")
    assert v["ok"] and v["plugin_id"] == "me.weather" and v["mismatches"] == []

    report = svc.test("me.weather")
    assert report["ok"] and report["passed"] >= 1, report["output_tail"]

    with pytest.raises(GuardError, match="does not match"):
        svc.register("me.weather", "deadbeef")
    svc.edit("me.weather", "backend/notes.md", "# changed\n")
    with pytest.raises(GuardError, match="changed since"):
        svc.register("me.weather", report["report_hash"])
    report = svc.test("me.weather")
    reg = svc.register("me.weather", report["report_hash"])
    assert reg["state"] == "registered" and reg["enabled"] is False
    rec = env["store"].read().plugins["me.weather"]
    assert rec.scope == "agent:a1" and rec.installed_by == "agent:a1" and rec.mode == "link" and not rec.enabled

    prop = svc.activate("me.weather", scope="agent")
    assert prop["status"] == "pending_user_approval"
    assert svc.list()["pending_proposals"] == [prop["proposal_id"]]
    decided = svc.apply_decision(prop["proposal_id"], "approved", by="u1")
    assert decided["restart_required"]
    rec = env["store"].read().plugins["me.weather"]
    assert rec.enabled and rec.scope == "agent:a1" and rec.permissions_acknowledged

    obs = svc.observe("me.weather")
    assert obs["enabled"] and obs["audit_events"] >= 5 and obs["ui_errors"] == 0
    assert svc.deactivate("me.weather")["enabled"] is False
    assert svc.diff("me.weather")["changed_files"] == 0
    hint = svc.publish_hint("me.weather")
    assert "steps" in hint and any("Release" in s for s in hint["steps"])

    # rollback twice → manual required; register/activate refuse
    env["store"].snapshot_lkg()  # the state a healthy boot proved — the rollback target
    svc.rollback("me.weather")
    r2 = svc.rollback("me.weather")
    assert r2["manual_required"] is True
    with pytest.raises(GuardError, match="human"):
        svc.activate("me.weather")


def test_register_requires_tests_and_version_bump(env, svc):
    svc.scaffold("me.weather", ["table"])
    with pytest.raises(GuardError, match="plugin_test first"):
        svc.register("me.weather", "x")
    report = svc.test("me.weather")
    svc.register("me.weather", report["report_hash"])
    # same version again → refused
    report = svc.test("me.weather")
    with pytest.raises(GuardError, match="version must increase"):
        svc.register("me.weather", report["report_hash"])


def test_heavy_kinds_need_agent_canary_before_global(env, svc):
    svc.scaffold("me.weather", ["hook"])
    report = svc.test("me.weather")
    svc.register("me.weather", report["report_hash"])
    with pytest.raises(GuardError, match="observed before global"):
        svc.activate("me.weather", scope="global")


def test_protected_and_cloud_refusals(env, svc, monkeypatch):
    for pid in ("builtin.chat", "builtin.nexus_plugins_module"):
        with pytest.raises(GuardError):
            svc.deactivate(pid)
        with pytest.raises(GuardError):
            svc.scaffold(pid, ["routes"])
    with pytest.raises(GuardError, match="another agent|not registered"):
        svc.observe("me.other")
    monkeypatch.setenv("NARRANEXUS_DEPLOYMENT_MODE", "cloud")
    from narranexus_plugins.nexus_plugins_module._nexus_plugins_impl.service import SelfExtensionService

    with pytest.raises(GuardError, match="local feature"):
        SelfExtensionService("a1", "u1", workspace=env["workspace"], store=env["store"])


def test_validate_flags_declared_vs_actual(env, svc):
    svc.scaffold("me.weather", ["routes"])
    svc.edit("me.weather", "backend/net.py", "import httpx\nimport subprocess\nimport os\nKEY = os.environ.get('K')\n")
    v = svc.validate("me.weather")
    assert v["ok"] and len(v["mismatches"]) == 3
    assert any("network" in m for m in v["mismatches"]) and any("subprocess" in m for m in v["mismatches"]) and any("environment" in m for m in v["mismatches"])


def test_budget_exhaustion(env, svc):
    from narranexus_plugins.nexus_plugins_module._nexus_plugins_impl.guards import REGISTER_BUDGET_PER_WINDOW

    for i in range(REGISTER_BUDGET_PER_WINDOW):
        svc.install(f"acme/p{i}")
    with pytest.raises(GuardError, match="budget"):
        svc.install("acme/px")
    assert len(svc.proposals.list(agent_id="a1", pending_only=True)) == REGISTER_BUDGET_PER_WINDOW


def test_install_and_upgrade_are_proposals(env, svc, monkeypatch):
    p = svc.install("acme/weather@1.0.0")
    assert p["status"] == "pending_user_approval"
    assert svc.proposals.get(p["proposal_id"]).extra["source"] == "acme/weather@1.0.0"
