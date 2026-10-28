"""
@file_name: test_hello_module_e2e.py
@author: Bin Liang
@date: 2026-09-04
@description: Batch 5 exit criterion — a non-builtin module installs as a plugin: linked through the CLI and booted, it is in the module registry, described in the decision prompt, gets its agent-level instance, is mounted by path on the MCP host, and takes part in a turn only once the owner enables it (plugin modules default off per agent).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from starlette.routing import Mount

from narranexus.cli.main import main as cli
from narranexus.hosts.boot import boot
from narranexus.kernel.plugins.importer import import_plugin_module, plugin_finder, uninstall_synthetic_package
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME, registry_path
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.builtins import load_builtins
from narranexus.platform.module_system.registry import ModuleRegistry

PLUGIN = Path(__file__).resolve().parent / "hello_module"
PID = "acme.hello_module"


@pytest.fixture
def home(tmp_path: Path, monkeypatch):
    (tmp_path / "home").mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(tmp_path / "home"))
    yield tmp_path / "home"
    uninstall_synthetic_package(PID)
    plugin_finder().unregister_deps(PID)


def test_a_module_plugin_installs_and_takes_part_in_a_turn(home: Path, db_client, monkeypatch):
    assert cli(["plugin", "link", str(PLUGIN)]) == 0
    regs = Registries()
    load_builtins(regs, "backend")
    report = boot("backend", registries=regs, cloud=False, host_version="1.19.0", store=RegistryStore(path=registry_path()))
    assert PID in report.user_plugin_ids and not report.isolated
    monkeypatch.setattr("narranexus.kernel.plugins.registries.KERNEL_REGISTRIES", regs)
    plugin = import_plugin_module(PID)

    # 1. the registry is the platform's only module table — the plugin module sits next to the builtins
    registry = ModuleRegistry(regs)
    assert registry["AcmeNotesModule"] is plugin.AcmeNotesModule and registry.owner_of("AcmeNotesModule") == PID and "ChatModule" in registry
    monkeypatch.setattr("narranexus.platform.module_system.registry.module_registry", registry)
    monkeypatch.setattr("narranexus.platform.module_system.module_registry", registry)

    # 2. the decision prompt and the display know it from its own declaration
    from narranexus.platform.agent_runtime._agent_runtime_steps.step_display import module_display
    from narranexus.platform.module_system._module_impl.instance_decision import module_overview_text
    from narranexus.platform.module_system._module_impl.metadata import get_all_modules_metadata

    assert "- **AcmeNotesModule**: Keeps the user's short notes" in module_overview_text()
    assert "## AcmeNotesModule" in get_all_modules_metadata() and module_display("AcmeNotesModule")["icon"] == "📝"

    # 3. its agent-level instance is created from its declaration, prefixed as declared
    from narranexus.platform.module_system import InstanceFactory

    instances = asyncio.run(InstanceFactory(db_client).create_agent_level_instances("agent_n"))
    notes_inst = next(i for i in instances if i.module_class == "AcmeNotesModule")
    assert notes_inst.instance_id.startswith("notes_") and notes_inst.description == "Personal notes"

    # 4. the MCP host mounts it by path — no port of its own
    from narranexus.platform.module_system.module_runner import ModuleRunner

    module = plugin.AcmeNotesModule("agent_n", "u1", db_client)
    server = ModuleRunner._build_host_server([("hello_module", module.build_instrumented_mcp_server())], 7801)
    assert {r.path for r in server.config.app.router.routes if isinstance(r, Mount)} == {"/mcp/hello_module"}
    assert asyncio.run(module.mcp_server()).server_url.endswith("/mcp/hello_module/sse")

    # 5. per-agent enablement: a plugin module is OFF for existing agents until the owner enables it
    from narranexus.platform.module_system._module_impl.loader import ModuleLoader
    from narranexus.platform.module_system.capability_service import CapabilityService
    from narranexus.platform.schema.module_schema import InstanceStatus, ModuleInstance

    loader = ModuleLoader(agent_id="agent_n", user_id="u1", database_client=db_client, module_map=dict(registry))
    insts = [ModuleInstance(instance_id=i.instance_id, module_class=i.module_class, description="", status=InstanceStatus.ACTIVE, agent_id="agent_n", dependencies=[]) for i in instances]
    assert "AcmeNotesModule" not in {i.module_class for i in asyncio.run(loader._drop_disabled(insts))}
    svc = CapabilityService(db_client, regs)
    asyncio.run(svc.set_enabled("agent_n", "AcmeNotesModule", True))
    bound = loader._create_module_objects(asyncio.run(loader._drop_disabled(insts)))
    notes = next(i.module for i in bound if i.module_class == "AcmeNotesModule")
    assert notes is not None and "note_add" in asyncio.run(notes.contribute_instructions(None))
    surface = asyncio.run(notes.contribute_tools(None))
    assert "hello_module" in surface.mcp_servers

    # 6. disabling the plugin removes the module from every view
    regs2 = Registries()
    load_builtins(regs2, "backend")
    assert "AcmeNotesModule" not in ModuleRegistry(regs2)
