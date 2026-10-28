"""
@file_name: test_provision.py
@author:
@date: 2026-08-10
@description: Regression guard for provision_new_agent (the shared new-agent
provisioning seam). The seam exists precisely so no caller silently drops a
step — most infamously default-skill install (pre-open review #3). These
tests pin the sequence CONTENT, its best-effort semantics, and the one
deliberate non-best-effort exception (peer-discovery), so a future edit that
re-drops a step or wraps peer-discovery in a swallow goes red.
"""
from __future__ import annotations

import asyncio

import pytest

import narranexus.platform.bootstrap.provision as prov


class _Spy:
    """Records calls; each attribute access returns an async recorder."""

    def __init__(self):
        self.calls: list[str] = []


@pytest.fixture
def wired(monkeypatch):
    spy = _Spy()

    class FakeAgentRepo:
        def __init__(self, db):
            pass

        async def add_agent(self, **kw):
            spy.calls.append("add_agent")

    class FakeInstanceFactory:
        def __init__(self, db):
            pass

        async def create_agent_level_instances(self, agent_id):
            spy.calls.append("instance_factory")

    async def fake_sync(db, agent_id):
        spy.calls.append("sync_agent_discovery")

    async def fake_apply_bootstrap(db, **kw):
        spy.calls.append("apply_bootstrap")

    class FakeProfile:
        name = "default"

    def fake_get_profile(name):
        return FakeProfile()

    class FakeSkillSvc:
        async def install_defaults(self, aid, uid):
            spy.calls.append("install_defaults")
            return {"failed": []}

    monkeypatch.setattr(prov, "AgentRepository", FakeAgentRepo)
    monkeypatch.setattr(
        "narranexus.platform.module_system._module_impl.instance_factory.InstanceFactory",
        FakeInstanceFactory,
    )
    monkeypatch.setattr(prov, "sync_agent_discovery", fake_sync)
    monkeypatch.setattr(
        "narranexus.platform.bootstrap.profiles.apply_bootstrap", fake_apply_bootstrap
    )
    monkeypatch.setattr(
        "narranexus.platform.bootstrap.profiles.get_profile", fake_get_profile
    )
    monkeypatch.setattr(
        "narranexus.platform.marketplace.skill_marketplace_service.SkillMarketplaceService",
        FakeSkillSvc,
    )
    return spy


async def _run(spy):
    res = await prov.provision_new_agent(
        object(), agent_id="agent_new", user_id="usr_1", agent_name="New"
    )
    # the fire-and-forget skill task needs a tick to run
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    return res


def test_sequence_calls_every_step_in_order(wired):
    res = asyncio.run(_run(wired))
    # All four load-bearing steps ran (install_defaults is the one review #3
    # found dropped from the old copies).
    for step in ("add_agent", "instance_factory", "sync_agent_discovery",
                 "apply_bootstrap", "install_defaults"):
        assert step in wired.calls, f"{step} was not called — a step got dropped"
    # Order: add_agent first, instances before discovery before bootstrap.
    assert wired.calls.index("add_agent") == 0
    assert wired.calls.index("instance_factory") < wired.calls.index("sync_agent_discovery")
    assert wired.calls.index("sync_agent_discovery") < wired.calls.index("apply_bootstrap")
    assert res.warnings == []


def test_best_effort_steps_swallow_and_warn(wired, monkeypatch):
    # instance_factory / bootstrap / skills raising must NOT abort the call;
    # each folds into warnings and provisioning continues.
    class Boom:
        def __init__(self, db):
            pass

        async def create_agent_level_instances(self, agent_id):
            raise RuntimeError("factory down")

    monkeypatch.setattr(
        "narranexus.platform.module_system._module_impl.instance_factory.InstanceFactory", Boom
    )
    res = asyncio.run(_run(wired))
    assert any("instance_factory" in w for w in res.warnings)
    # the rest still ran
    assert "sync_agent_discovery" in wired.calls
    assert "apply_bootstrap" in wired.calls


def test_peer_discovery_failure_propagates(wired, monkeypatch):
    # The ONE deliberately non-best-effort step: a peer-discovery failure must
    # bubble out of provision_new_agent (auth.py's historical behaviour), so a
    # future "helpful" try/except around it turns this red.
    async def boom_sync(db, agent_id):
        raise RuntimeError("discovery down")

    monkeypatch.setattr(prov, "sync_agent_discovery", boom_sync)
    with pytest.raises(RuntimeError, match="discovery down"):
        asyncio.run(
            prov.provision_new_agent(
                object(), agent_id="a", user_id="u", agent_name="N"
            )
        )


def test_add_agent_failure_aborts(wired, monkeypatch):
    # Step 0 is not best-effort either: a duplicate/failed insert means there
    # is no agent to provision.
    class Boom:
        def __init__(self, db):
            pass

        async def add_agent(self, **kw):
            raise RuntimeError("dup id")

    monkeypatch.setattr(prov, "AgentRepository", Boom)
    with pytest.raises(RuntimeError, match="dup id"):
        asyncio.run(
            prov.provision_new_agent(
                object(), agent_id="a", user_id="u", agent_name="N"
            )
        )


def test_unsafe_agent_id_is_rejected_before_any_write(wired):
    # Path-traversal defense (铁律 #5): an agent_id that isn't a safe token must
    # raise BEFORE add_agent / workspace creation — the id becomes a filesystem
    # path segment (base/{user_id}/{agent_id}), so "../victim/agent" could write
    # into another tenant's workspace. This is the ONE seam every caller funnels
    # through, so the guard belongs here.
    for bad in ("../u2/agent_x", "a/b", "x..y", "with space", "semi;colon", "", "agent_ok\n", "\nagent_ok"):
        with pytest.raises(ValueError):
            asyncio.run(prov.provision_new_agent(
                object(), agent_id=bad, user_id="usr_1", agent_name="New"
            ))
    assert wired.calls == []  # nothing created for any rejected id


def test_safe_agent_id_passes_the_guard(wired):
    # The tool-minted form agent_<12hex> and other safe tokens must pass.
    for good in ("agent_0123456789ab", "agent_new", "abc_DEF-123"):
        wired.calls.clear()
        asyncio.run(prov.provision_new_agent(
            object(), agent_id=good, user_id="usr_1", agent_name="New"
        ))
        assert wired.calls[0] == "add_agent"
