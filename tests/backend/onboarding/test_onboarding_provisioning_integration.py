"""
@file_name: test_onboarding_provisioning_integration.py
@author: Bin Liang
@date: 2026-08-19
@description: Integration test for ensure_guide_agent — the REAL path, no
collaborator mocks (only the marketplace skill install is absent because the
fixture registry is unseeded). The unit tests in
test_onboarding_provisioning.py pin the parameter pipes; this file pins the
PERSISTED contract, so a drift in TriggerConfig fields, bootstrap metadata
keys, or the provision seam goes red here instead of silently degrading every
new user (all post-row steps are best-effort warnings in production).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from backend.onboarding import provisioning as ob
from backend.onboarding.personas import PERSONAS
from narranexus.platform.repository.user_repository import UserRepository


@pytest.mark.asyncio
async def test_real_provisioning_persists_the_full_contract(
    db_client, tmp_path, monkeypatch
):
    from narranexus.platform.settings import settings

    monkeypatch.setenv(ob.ENV_FLAG, "1")
    monkeypatch.setattr(settings, "base_working_path", str(tmp_path / "ws"))

    user_repo = UserRepository(db_client)
    await user_repo.add_user(user_id="u_int", user_type="individual")

    res = await ob.ensure_guide_agent(db_client, "u_int", is_new_user=True)

    assert res["provisioned"] is True
    agent_id = res["agent_id"]
    # The ONLY tolerated warning is the guide-skill install (the fixture's
    # marketplace registry is unseeded). Anything else — bootstrap, tagging,
    # job — means a silently degraded guide in production.
    assert [w for w in res["warnings"] if not w.startswith("guide_skill")] == []

    # agents row + metadata contract.
    row = await db_client.get_one("agents", {"agent_id": agent_id})
    assert row is not None and row["created_by"] == "u_int"
    meta = json.loads(row["agent_metadata"])
    assert meta["provisioned_source"] == "onboarding"
    assert meta["bootstrap_profile"] == "onboarding"
    greeting = meta["bootstrap_greeting"]
    assert res["agent_name"] in greeting
    assert "Daily check-in" in greeting
    assert "三件小事" in greeting  # the ZH half of the bilingual greeting
    persona = next(p for p in PERSONAS if p["key"] == res["persona"])
    assert persona["tagline_en"] in greeting

    # Bootstrap.md really landed in the workspace (drives bootstrap_active →
    # the frontend's instant greeting).
    ws = Path(settings.base_working_path)
    bootstrap_files = list(ws.rglob("Bootstrap.md"))
    assert len(bootstrap_files) == 1

    # The daily check-in job row.
    job_row = await db_client.get_one("instance_jobs", {"agent_id": agent_id})
    assert job_row is not None
    assert job_row["job_type"] == "scheduled"
    assert job_row["status"] in ("pending", "active")
    tc = json.loads(job_row["trigger_config"])
    assert tc["interval_seconds"] == 86400
    assert tc["end_at"]  # platform-enforced horizon persisted with the job
    payload = job_row["payload"]
    assert "three consecutive" in payload and "job_update" in payload

    # User-level marker written (top-level key).
    user = await user_repo.get_user("u_int")
    assert user.metadata[ob.GUIDE_METADATA_FLAG] is True

    # Second call: warm-path marker skip, no second agent. (is_new_user=True
    # so the marker check — not the default-off backfill brake — decides.)
    res2 = await ob.ensure_guide_agent(db_client, "u_int", is_new_user=True)
    assert res2 == {"skipped": "already_provisioned"}
