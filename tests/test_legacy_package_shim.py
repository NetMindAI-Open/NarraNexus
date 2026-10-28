"""
@file_name: test_legacy_package_shim.py
@author: Bin Liang
@date: 2026-09-04
@description: Batch 6a (D8) — `xyz_agent_context` is a one-release alias of `narranexus.platform`: every old dotted path resolves to the SAME module object (module → module_system), the root re-exports match, one DeprecationWarning is emitted, the by-path / -m entrypoint shims delegate to the moved main(), `python -m` really resolves (runpy's get_code path), and the alias refuses to import once the host reaches REMOVED_AT.
"""
from __future__ import annotations

import importlib
import subprocess
import sys
import warnings
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]


def test_old_paths_alias_the_platform_modules():
    import narranexus.platform.module_system.base as new_base
    import narranexus.platform.schema.hook_schema as new_hook

    import xyz_agent_context

    xyz_agent_context._warned = False  # another test may already have paid the once-per-process warning
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        old_base = importlib.import_module("xyz_agent_context.module.base")
        old_hook = importlib.import_module("xyz_agent_context.schema.hook_schema")
        importlib.import_module("xyz_agent_context.utils.db.database")
    assert old_base is new_base and old_hook is new_hook
    assert old_base.XYZBaseModule is new_base.XYZBaseModule
    assert any(issubclass(w.category, DeprecationWarning) and "narranexus.platform" in str(w.message) for w in caught)

    from narranexus.platform import AgentRuntime, XYZBaseModule

    assert xyz_agent_context.XYZBaseModule is XYZBaseModule and xyz_agent_context.AgentRuntime is AgentRuntime
    assert sys.modules["xyz_agent_context.module.base"] is new_base
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("xyz_agent_context.no_such_package")


def test_warning_is_emitted_once_per_process():
    code = (
        "import warnings; warnings.simplefilter('always'); seen=[]\n"
        "with warnings.catch_warnings(record=True) as w:\n"
        "    import xyz_agent_context.narrative, xyz_agent_context.schema, xyz_agent_context.module.base\n"
        "print(sum(1 for x in w if x.category is DeprecationWarning and 'alias' in str(x.message)))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=REPO, check=True).stdout.strip()
    assert out == "1"


def test_entrypoint_shims_delegate_to_the_moved_mains():
    from narranexus.platform.module_system import module_runner, run_worker_supervisor
    from narranexus.platform.utils.db import sqlite_proxy_server

    for shim, target in (
        ("xyz_agent_context.module.module_runner", module_runner),
        ("xyz_agent_context.module.run_worker_supervisor", run_worker_supervisor),
        ("xyz_agent_context.utils.db.sqlite_proxy_server", sqlite_proxy_server),
    ):
        assert importlib.import_module(shim).main is target.main
    # the by-path shim the deploy compose runs
    path_shim = REPO / "src/xyz_agent_context/module/module_runner.py"
    assert path_shim.exists() and "narranexus.platform.module_system.module_runner import main" in path_shim.read_text()
    assert module_runner.main(["help"]) == 0


# Every ``-m`` spelling the deploy repo (compose.yml, Dockerfile.executor) and the
# gate scripts use. ``runpy._get_module_details`` is exactly what ``python -m``
# runs before executing anything: it must hand back a code object from the
# platform file. This is the path that was dead while the import-only test
# above stayed green (the loader had no ``get_code``).
DEPLOY_M_ENTRYPOINTS = (
    ("xyz_agent_context.module.run_worker_supervisor", "module_system/run_worker_supervisor.py"),
    ("xyz_agent_context.module.run_channel_triggers", "module_system/run_channel_triggers.py"),
    ("xyz_agent_context.services.model_sync_runner", "services/model_sync_runner.py"),
    ("xyz_agent_context.agent_runtime.executor_service", "agent_runtime/executor_service.py"),
    ("xyz_agent_context.utils.db.sqlite_proxy_server", "utils/db/sqlite_proxy_server.py"),
)


@pytest.mark.parametrize("name,origin_suffix", DEPLOY_M_ENTRYPOINTS)
def test_python_dash_m_resolves_every_deploy_entrypoint(name, origin_suffix):
    code = (
        "import runpy, sys, types\n"
        f"mod_name, spec, code = runpy._get_module_details({name!r})\n"
        "assert isinstance(code, types.CodeType), type(code)\n"
        "print(spec.origin)"
    )
    out = subprocess.run([sys.executable, "-W", "ignore", "-c", code], capture_output=True, text=True, cwd=REPO, timeout=120)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip().replace("\\", "/").endswith("narranexus/platform/" + origin_suffix)


def test_python_dash_m_runs_the_supervisor_help():
    out = subprocess.run(
        [sys.executable, "-W", "ignore", "-m", "xyz_agent_context.module.run_worker_supervisor", "--help"],
        capture_output=True, text=True, cwd=REPO, timeout=120,
    )
    assert out.returncode == 0, out.stderr[-2000:]
    assert "usage:" in out.stdout


def test_gate_script_shims_exist():
    """The deploy repo's check_trigger_alignment.sh greps these files by path."""
    module_dir = REPO / "src/xyz_agent_context/module"
    assert (module_dir / "run_channel_triggers.py").exists()
    assert "start_channel_triggers" in (module_dir / "run_worker_supervisor.py").read_text()


def test_alias_refuses_to_import_once_expired(monkeypatch):
    import xyz_agent_context
    from narranexus.kernel.plugins import compat

    monkeypatch.setattr(compat, "host_version", lambda: xyz_agent_context.REMOVED_AT)
    with pytest.raises(ImportError, match="gone as of"):
        xyz_agent_context._refuse_if_expired()
    monkeypatch.setattr(compat, "host_version", lambda: "1.21.0")
    xyz_agent_context._refuse_if_expired()
