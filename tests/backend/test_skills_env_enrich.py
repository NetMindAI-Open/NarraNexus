"""
@file_name: test_skills_env_enrich.py
@date: 2026-08-13
@description: _enrich_platform_env_status downgrades ONLY the platform-assumed
half, and only when the DB can't satisfy it.

Regressions locked here:
- It must NOT re-read stored meta by name (that missed .disabled/ dirs and
  false-flagged disabled skills). _FakeModule asserts the arg is never read.
- It must NOT downgrade a self-stored platform var (a user who manually entered
  NETMIND_API_KEY in the Skill tab stays configured even with no provider row).
  This is driven by SkillInfo.env_platform_assumed, which excludes self-stored
  platform vars at parse time.
"""
from __future__ import annotations

import pytest

from narranexus_plugins.skill_module import routes as skills_routes
from narranexus.platform.schema.skill_schema import SkillInfo


class _FakeModule:
    """enrich must NOT read stored config, so it must never touch this."""

    def get_skill_env_config(self, name):  # pragma: no cover - must not be called
        raise AssertionError("enrich re-read stored meta — it should not")

    def _read_skill_meta(self, name):  # pragma: no cover - must not be called
        raise AssertionError("enrich re-read stored meta — it should not")


def _skill(name, *, requires_env, env_configured, platform_assumed, disabled=False):
    return SkillInfo(
        name=name, description="", path=f"skills/{name}", disabled=disabled,
        requires_env=requires_env, env_configured=env_configured,
        env_platform_assumed=platform_assumed,
    )


def _patch_available(monkeypatch, vars_available):
    async def _available(db, user_id):
        return set(vars_available)

    async def _db():
        return object()

    monkeypatch.setattr(
        "narranexus.platform.utils.db.db_factory.get_db_client", _db, raising=False
    )
    monkeypatch.setattr(
        "narranexus_plugins.skill_module.skill_module.platform_env_available",
        _available,
    )


@pytest.mark.asyncio
async def test_self_stored_platform_var_never_downgraded(monkeypatch):
    # 🟡1 regression: user manually entered NETMIND_API_KEY in the Skill tab
    # (so it is self-stored → platform_assumed is None), and has NO netmind
    # provider row. It must STAY configured — the enrich pass must not touch it.
    _patch_available(monkeypatch, set())  # platform can satisfy nothing

    # A disabled skill, to also prove enrich never resolves by name / dir.
    kept = _skill(
        "netmind-vision", requires_env=["NETMIND_API_KEY"],
        env_configured=True, platform_assumed=None, disabled=True,
    )
    await skills_routes._enrich_platform_env_status(_FakeModule(), [kept], "u1")
    assert kept.env_configured is True


@pytest.mark.asyncio
async def test_platform_assumed_var_downgraded_only_when_unavailable(monkeypatch):
    # A skill whose NETMIND_API_KEY is NOT self-stored → its "configured" rests
    # purely on the platform assumption (platform_assumed=[var]).
    dropped = _skill(
        "assumed-missing", requires_env=["NETMIND_API_KEY"],
        env_configured=True, platform_assumed=["NETMIND_API_KEY"],
    )
    kept = _skill(
        "assumed-present", requires_env=["NETMIND_API_KEY"],
        env_configured=True, platform_assumed=["NETMIND_API_KEY"],
    )

    _patch_available(monkeypatch, set())  # provider row absent
    await skills_routes._enrich_platform_env_status(_FakeModule(), [dropped], "u1")
    assert dropped.env_configured is False  # assumption unmet → downgraded

    _patch_available(monkeypatch, {"NETMIND_API_KEY"})  # provider row present
    await skills_routes._enrich_platform_env_status(_FakeModule(), [kept], "u1")
    assert kept.env_configured is True  # assumption satisfied → left alone
