"""
@file_name: test_host_entrypoints_boot.py
@author: Bin Liang
@date: 2026-09-07
@description: Every host process actually boots the plugin platform, and declares itself healthy only once it serves.

Two separate claims, both previously untested:

1. **The five entrypoints call boot.** Deleting ``boot_executor_plugins()`` from
   the executor's lifespan (or the equivalent line in the mcp runner, the
   workers supervisor, the channels supervisor, the backend lifespan) left the
   whole suite green: ``test_turn_loaded_for_every_turn_host`` proves that
   *calling boot* fills the slots, never that the entrypoint calls it. The
   registries would simply be empty in production.
2. **Health is declared after the host serves, not at the end of boot.**
   ``BootReport.mark_healthy`` clears the boot-crash marker AND moves the
   last-known-good snapshot, so firing it inside ``_boot`` meant a process that
   populated its registries and then died before serving still advanced the
   rollback target — the exact state safe mode exists to escape.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from narranexus.platform.module_system import plugins_boot

REPO = Path(__file__).resolve().parents[3]

# entrypoint file -> (function that must call it, boot function name)
ENTRYPOINTS = {
    "src/narranexus/platform/agent_runtime/executor_service.py": ("_lifespan", "boot_executor_plugins"),
    "src/narranexus/platform/module_system/module_runner.py": (None, "boot_mcp_plugins"),
    "src/narranexus/platform/module_system/run_worker_supervisor.py": ("run", "boot_worker_plugins"),
    "src/narranexus/platform/module_system/run_channel_triggers.py": ("main", "boot_channel_plugins"),
    "backend/main.py": ("lifespan", "boot_backend_plugins"),
    # The NexusPower turn runner is its own process (spawned per user by the
    # adapter): it resolves the framework's seats from the registries and so
    # must boot like every other host — found by the 2026-09-08 local e2e pass.
    # It lives one turn, so it takes the read-only boot (no boot marker).
    "plugins/builtin.frameworks.nexus_power/src/narranexus_plugins/frameworks_nexus_power/core/runner.py": ("main", "boot_turn_plugins"),
}


def _called_names(tree: ast.AST) -> set[str]:
    return {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


def _function(tree: ast.Module, name: str) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"no function named {name!r}")


@pytest.mark.parametrize("relative", sorted(ENTRYPOINTS))
def test_every_host_entrypoint_boots_the_plugin_platform(relative: str):
    """A host that does not boot has empty registries: no frameworks, no
    providers, no triggers — and every seam raises ``UnknownEntry`` at the
    first request instead of at startup."""
    func_name, boot_name = ENTRYPOINTS[relative]
    path = REPO / relative
    assert path.is_file(), path
    tree = ast.parse(path.read_text(encoding="utf-8"))
    scope = tree if func_name is None else _function(tree, func_name)
    assert boot_name in _called_names(scope), f"{relative}: {func_name or '<module>'} does not call {boot_name}()"


def test_the_entrypoint_table_covers_every_boot_function_the_platform_offers():
    """The parametrisation above is only worth its runtime if it enumerates the
    whole set: a sixth host role added to ``plugins_boot`` must land here too."""
    offered = {name for name in plugins_boot.__all__ if name.startswith("boot_")}
    covered = {boot for _, boot in ENTRYPOINTS.values()}
    assert offered <= covered, sorted(offered - covered)
    assert len(ENTRYPOINTS) == 6


def test_booting_the_agent_side_does_not_declare_the_host_healthy(monkeypatch):
    """``_boot`` used to end with ``report.mark_healthy()``.

    The marker and the last-known-good snapshot are the rollback machinery: they
    may only move once the process serves. This drives the REAL ``_boot`` with a
    stubbed ``boot`` and asserts it leaves them alone; putting the call back
    turns this red.
    """
    from narranexus.hosts.boot import BootReport
    from narranexus.kernel.plugins.registries import Registries
    from narranexus.platform import bindings_runtime

    marked: list[str] = []

    class _Recorder(BootReport):
        def mark_healthy(self) -> None:
            marked.append(self.role)

    report = _Recorder(role="workers")
    monkeypatch.setattr(plugins_boot, "_REPORTS", {})
    monkeypatch.setattr(plugins_boot, "KERNEL_REGISTRIES", Registries())
    monkeypatch.setattr(plugins_boot, "boot", lambda *a, **k: report)
    monkeypatch.setattr(bindings_runtime, "resolve_runtime_bindings", lambda *a, **k: None)

    assert plugins_boot.boot_worker_plugins() is report
    assert marked == [], "the boot declared itself healthy before the host served anything"

    plugins_boot.mark_host_healthy("workers")
    assert marked == ["workers"]
    plugins_boot.mark_host_healthy("mcp")  # a role that never booted is a no-op, never a crash
    assert marked == ["workers"]


def test_the_executor_boots_before_serving_and_marks_healthy_only_at_the_end(monkeypatch):
    """Drive the executor's real lifespan: boot first, health last.

    Reordering the two (or dropping either) is the regression; the assertion is
    on the ORDER of the recorded calls, not on their presence.
    """
    import asyncio

    from narranexus.platform.agent_runtime import executor_service

    calls: list[str] = []
    monkeypatch.setattr(plugins_boot, "boot_executor_plugins", lambda: calls.append("boot"))
    monkeypatch.setattr(plugins_boot, "mark_host_healthy", lambda role: calls.append(f"healthy:{role}"))
    monkeypatch.setenv("EXECUTOR_PREWARM_FRAMEWORKS", "")  # no warmup work in a test process

    async def _startup() -> None:
        async with executor_service._lifespan(executor_service.app):
            calls.append("serving")

    asyncio.run(_startup())
    assert calls == ["boot", "healthy:workers", "serving"]


def test_the_per_turn_runner_boot_never_touches_the_boot_marker(monkeypatch, tmp_path):
    """The local NexusPower runner is one process per turn and exits by design,
    so it can never ``mark_host_healthy``. Booting it like a long-lived host
    left a workers marker behind on every turn: the third conversation read
    "3 consecutive boots of workers never reached health" and the app went
    into SAFE MODE (2026-09-08 local e2e). The per-turn boot is the read-only
    (``inspect``) form — no marker, no registry writes."""
    from narranexus.hosts.boot import BootReport
    from narranexus.kernel.plugins.registries import Registries
    from narranexus.platform import bindings_runtime

    seen: dict[str, object] = {}

    def _boot(role, **kw):
        seen["role"] = role
        seen["inspect"] = kw.get("inspect")
        return BootReport(role=role)

    monkeypatch.setattr(plugins_boot, "_REPORTS", {})
    monkeypatch.setattr(plugins_boot, "KERNEL_REGISTRIES", Registries())
    monkeypatch.setattr(plugins_boot, "boot", _boot)
    monkeypatch.setattr(bindings_runtime, "resolve_runtime_bindings", lambda *a, **k: None)

    plugins_boot.boot_turn_plugins()
    assert seen == {"role": "workers", "inspect": True}

    # The long-lived executor keeps the writable boot (it does reach health).
    monkeypatch.setattr(plugins_boot, "_REPORTS", {})
    plugins_boot.boot_executor_plugins()
    assert seen == {"role": "workers", "inspect": False}


def test_a_real_per_turn_boot_leaves_no_marker_behind(monkeypatch, tmp_path):
    """End to end on the real ``boot``: an inspect boot must not create
    ``.booting-workers`` — that file is what counts failed boots."""
    from narranexus.hosts import boot as boot_mod
    from narranexus.kernel.plugins.loader import Discovery
    from narranexus.kernel.plugins.paths import boot_marker_path
    from narranexus.kernel.plugins.registries import Registries

    monkeypatch.setenv("NARRANEXUS_PLUGIN_HOME", str(tmp_path / "plugins"))
    monkeypatch.setattr(boot_mod, "discover", lambda **kw: Discovery(manifests=[]))
    boot_mod.boot("workers", registries=Registries(), cloud=False, host_version="1.0.0", inspect=True)
    assert not boot_marker_path("workers").exists()
    boot_mod.boot("workers", registries=Registries(), cloud=False, host_version="1.0.0")
    assert boot_marker_path("workers").read_text() == "0"

