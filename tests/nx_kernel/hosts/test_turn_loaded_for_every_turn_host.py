"""
@file_name: test_turn_loaded_for_every_turn_host.py
@author: Bin Liang
@date: 2026-09-04
@description: Every process that runs turns (backend, mcp, workers) gets the pipeline, the seven stage slots, the profiles and the frameworks from its BOOT — there is no lazy registration any more — and an entry nobody registered fails with a message that says whether the platform was booted at all.
"""
from __future__ import annotations

import pytest

from narranexus.contracts import PluginError, RegistryFrozen, UnknownEntry
from narranexus.contracts.agent.stages import Stage
from narranexus.hosts.boot import boot
from narranexus.kernel.plugins.registries import Registries
from narranexus.platform.turn.stages import slot_path


@pytest.mark.parametrize("role", ["backend", "mcp", "workers"])
def test_turn_and_frameworks_are_loaded_by_every_turn_running_role(plugin_home, role):
    regs = Registries()
    report = boot(role, registries=regs, cloud=False, host_version="1.15.0")
    assert regs.frozen and not report.builtins.errors
    # the production invariant: nothing registers after boot
    with pytest.raises(RegistryFrozen):
        regs.registry_for("turn.pipeline.recall").register("late", lambda: 1, owner="acme.late")
    assert "builtin.turn" in {e.owner for e in regs.registry_for("turn.pipeline").entries()}
    for stage in Stage:
        assert regs.registry_for(slot_path(stage)).names(), stage
    assert regs.registry_for("turn.profiles").names()
    assert {"nexus_power", "claude_code", "codex_cli"} <= set(regs.registry_for("turn.pipeline.act.framework").names())
    # the NexusPower seats hang under the framework slot and are populated by the same boot
    assert regs.registry_for("turn.pipeline.act.framework.nexus_power.policy").names() == ("disallowed_tools", "workspace_confinement", "shell_confinement")


def test_an_unbooted_process_says_so(plugin_home):
    regs = Registries()
    with pytest.raises(UnknownEntry, match="booted the plugin platform"):
        regs.registry_for("turn.pipeline.recall").get("narrative_llm")


def test_a_booted_registries_is_frozen_against_every_late_registration(plugin_home):
    """The suite's process-global ``KERNEL_REGISTRIES`` is deliberately loaded
    at collection and never frozen, so ``Registry.register``'s frozen branch is
    only reachable through a private ``Registries`` that a real boot froze.
    This is that one session: every surface a plugin can register into must
    refuse afterwards, including a slot whose registry did not exist yet at
    freeze time (``registry_for`` creates it lazily and must hand back a frozen
    one — otherwise "registration only at boot" has a hole exactly the size of
    every unused slot)."""
    from narranexus.kernel.plugins.hooks import HookSpec
    from narranexus.contracts.services import ServiceRef

    regs = Registries()
    boot("backend", registries=regs, cloud=False, host_version="1.15.0")
    assert regs.frozen

    used = "turn.pipeline.recall"
    assert used in regs.paths()
    with pytest.raises(RegistryFrozen):
        regs.registry_for(used).register("late", lambda: 1, owner="acme.late")

    unused = next(path for path in regs.slots.paths() if path not in regs.paths() and regs.slots.get(path).arity == "many")
    with pytest.raises(RegistryFrozen):
        regs.registry_for(unused).register("late", lambda: 1, owner="acme.late")

    with pytest.raises(RegistryFrozen):
        regs.hooks.add("onDidStartRun", lambda run_id: None, owner="acme.late")
    # ``declare`` after freeze is allowed on purpose: naming a hook adds no
    # behaviour, and the caller it hands back is born frozen — assert THAT.
    assert regs.hooks.declare(HookSpec("onSomethingNew", ("x",))).frozen
    # the locator raises the generic PluginError, not RegistryFrozen
    with pytest.raises(PluginError, match="locator is frozen"):
        regs.services.expose(ServiceRef("acme.late.thing"), object(), owner="acme.late")
