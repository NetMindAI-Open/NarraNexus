"""
@file_name: test_registries_hooks.py
@author: Bin Liang
@date: 2026-09-03
@description: Every Registries instance declares the host event and stage hook vocabulary; re-declaring an identical spec is a no-op, a different one conflicts.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import RegistryConflict
from narranexus.contracts.agent.events import STAGE_HOOKS
from narranexus.contracts.events import HOST_EVENTS, host_event_params
from narranexus.kernel.plugins.hooks import HookSpec
from narranexus.kernel.plugins.registries import Registries


def test_kernel_declares_the_hook_vocabulary():
    regs = Registries()
    assert set(regs.hooks.names()) == set(HOST_EVENTS) | set(STAGE_HOOKS)
    assert regs.hooks.specs()["onDidPersistTurn"].params == host_event_params("onDidPersistTurn")
    assert regs.hooks.specs()["onWillAct"].firstresult is True


def test_identical_redeclare_is_idempotent_and_different_conflicts():
    regs = Registries()
    spec = regs.hooks.specs()["onDidStartRun"]
    assert regs.hooks.declare(HookSpec(spec.name, spec.params, spec.firstresult, spec.doc)) is regs.hooks.caller("onDidStartRun")
    with pytest.raises(RegistryConflict, match="different signature"):
        regs.hooks.declare(HookSpec("onDidStartRun", ("run_id",)))


def test_same_owner_reregistering_a_name_is_idempotent_but_other_owners_conflict():
    """After a module re-import the loader produces fresh Contribution objects for the same (owner, name)."""
    from narranexus.contracts._base import RegistryConflict
    from narranexus.kernel.plugins.registries import Registries
    from narranexus.kernel.plugins.registry import Contribution

    regs = Registries()
    reg = regs.registry_for("backend.routes")
    first = Contribution("api", lambda: "first")
    again = Contribution("api", lambda: "again")  # different object, same owner + name
    reg.register_contribution(first, owner="acme.a")
    reg.register_contribution(again, owner="acme.a")
    assert reg.get("api") == "first" and len(reg.entries()) == 1
    with pytest.raises(RegistryConflict):
        reg.register_contribution(Contribution("api", lambda: "other"), owner="acme.b")
    regs.freeze()
    reg.register_contribution(Contribution("api", lambda: "after-freeze"), owner="acme.a")  # still a no-op
    assert reg.get("api") == "first"
