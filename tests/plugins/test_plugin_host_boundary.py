"""
@file_name: test_plugin_host_boundary.py
@author: Bin Liang
@date: 2026-09-07
@description: No builtin plugin imports the host or a private platform module, and every plugin's wheel metadata says what it actually depends on.

`lint-imports` enforces the same two lines, but it is a separate CI step over
a whole-repo graph: this file puts the boundary in the suite that a plugin
author actually runs, and reports the offending FILE rather than a module pair.
"""
from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
PLUGINS = sorted(p for p in (REPO / "plugins").iterdir() if (p / "pyproject.toml").exists())


def _imported_modules(py: Path) -> set[str]:
    """Every dotted module path this file imports, function-level ones included."""
    tree = ast.parse(py.read_text(), filename=str(py))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.add(node.module)
    return found


def _sources() -> list[Path]:
    files = [f for p in PLUGINS for f in (p / "src").rglob("*.py")]
    assert len(files) > 200, f"the sweep found only {len(files)} plugin sources — the layout moved"
    return files


def test_no_plugin_imports_the_host():
    """A plugin that imports ``backend.*`` is a builtin privilege a third party cannot copy.

    Thirteen routers moved into plugin packages in batch 6b and kept importing
    `backend.routes._ownership`, `backend.auth`, `backend.auth_errors` and
    `backend.config` — six of them underscore-private. What a router needs from
    the host is `narranexus.sdk.web` (contracts.web.WebHost).
    """
    offenders = [
        f"{f.relative_to(REPO)} -> {m}"
        for f in _sources()
        for m in _imported_modules(f)
        if m == "backend" or m.startswith("backend.")
    ]
    assert offenders == [], "plugins must reach the host through narranexus.sdk.web:\n" + "\n".join(offenders)


def test_no_plugin_imports_a_private_platform_module():
    """An `_`-prefixed platform module is private even inside the platform.

    Builtins may import `narranexus.platform` PUBLIC modules (same repo, same
    release — docs/API_POLICY.md §1.1); a private one needs a named public
    function on the owning package's facade instead.
    """
    private = re.compile(r"^narranexus\.platform\.[A-Za-z0-9_.]*\._[A-Za-z0-9_]")
    offenders = [
        f"{f.relative_to(REPO)} -> {m}"
        for f in _sources()
        for m in _imported_modules(f)
        if private.match(m)
    ]
    assert offenders == [], "add a public function to the owning package's facade instead:\n" + "\n".join(offenders)


def test_the_public_seams_those_imports_moved_to_exist():
    """The facades the sweep above pushed callers onto — deleting one must fail here, not at runtime."""
    import narranexus.platform.module_system as module_system
    from narranexus.platform.agent_framework.adapters import build_tool_policy_guard
    from narranexus.platform.agent_framework.llm import prompt_probe_emit
    from narranexus.platform.marketplace import ArtifactStore, InstallPipeline, get_secret_box, get_template_store

    for name in (
        "caller_user_id_from_request",
        "caller_team_id_from_request",
        "caller_event_id_from_request",
        "caller_root_run_id",
        "caller_turn_source",
        "caller_errand_scope",
        "resolve_caller_agent_id",
    ):
        assert name in module_system.__all__ and hasattr(module_system, name), name
    assert all(callable(x) for x in (build_tool_policy_guard, prompt_probe_emit, get_secret_box, get_template_store))
    assert isinstance(ArtifactStore, type) and isinstance(InstallPipeline, type)


def test_every_plugin_declares_its_contract_dependencies():
    """`dependencies = []` was a lie: these packages import the contracts and the SDK."""
    for plugin in PLUGINS:
        data = tomllib.loads((plugin / "pyproject.toml").read_text())
        deps = data["project"]["dependencies"]
        assert deps == ["narranexus-contracts", "narranexus-sdk"], f"{plugin.name}: {deps}"


def test_the_channel_template_imports_only_the_sdk():
    """`plugin new --kinds channel` must not scaffold a third party onto non-public API."""
    template = REPO / "templates" / "channel" / "backend" / "__init__.py"
    modules = _imported_modules(template)
    assert {m for m in modules if m.startswith("narranexus")} == {"narranexus.sdk"}, sorted(modules)
