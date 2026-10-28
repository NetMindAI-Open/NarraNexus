"""
@file_name: test_templates_and_cli.py
@author: Bin Liang
@date: 2026-09-03
@description: Every template scaffolds into a plugin whose own tests pass under PluginTestHost; CLI verbs behave; publish-check catches misses.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

from narranexus.cli.main import main as cli
from narranexus.cli.scaffold import TEMPLATES_DIR, scaffold
from narranexus.kernel.plugins.importer import plugin_finder, uninstall_synthetic_package
from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME

KINDS = sorted(p.name for p in TEMPLATES_DIR.iterdir() if p.is_dir())


@pytest.fixture
def home(tmp_path: Path, monkeypatch):
    h = tmp_path / "home"
    h.mkdir()
    monkeypatch.setenv(ENV_PLUGIN_HOME, str(h))
    yield h


def test_every_template_kind_exists():
    assert KINDS == [
        "bundle", "channel", "context_provider", "framework", "hook", "mcp_server", "pipeline_profile", "routes",
        "settings", "skill", "stage_strategy", "table", "theme", "tool", "ui_page", "ui_panel", "worker",
    ]


@pytest.mark.parametrize("kind", KINDS)
def test_template_scaffolds_and_its_tests_pass(kind: str, tmp_path: Path, home: Path):
    pid = f"acme.t_{kind}"
    dest = tmp_path / pid
    files = scaffold(pid, [kind], dest, display_name=f"T {kind}")
    assert (dest / "narranexus-plugin.json").is_file() and (dest / "backend" / "__init__.py").is_file()
    assert not any("__PLUGIN" in f.read_text(errors="ignore") for f in files if f.suffix in (".py", ".json", ".md", ".ts", ".js"))
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(dest / "tests")],
        capture_output=True, text=True, cwd=str(Path(__file__).resolve().parents[2]), env={**__import__("os").environ, ENV_PLUGIN_HOME: str(home / kind)},
        timeout=300,
    )
    assert proc.returncode == 0, proc.stdout[-3000:] + proc.stderr[-3000:]
    uninstall_synthetic_package(pid)
    plugin_finder().unregister_deps(pid)


def test_scaffold_composes_kinds_and_cli_new(tmp_path: Path, capsys):
    dest = tmp_path / "acme.multi"
    assert cli(["plugin", "new", "acme.multi", "--kinds", "routes,table,settings", "--dir", str(dest)]) == 0
    import json

    manifest = json.loads((dest / "narranexus-plugin.json").read_text())
    assert set(manifest["provides"]) == {"backend.routes", "backend.tables", "backend.settings"}
    assert manifest["api"] == {"route": 0, "table": 0, "settings": 0}
    init = (dest / "backend" / "__init__.py").read_text()
    assert "ROUTES = " in init and "TABLES = " in init and "SETTINGS = " in init
    assert cli(["plugin", "new", "builtin.x"]) == 2
    assert cli(["plugin", "new", "acme.multi", "--dir", str(dest)]) == 2  # not empty
    assert cli(["plugin", "new", "acme.y", "--kinds", "nope", "--dir", str(tmp_path / "y")]) == 2


def test_publish_check_reports_misses(tmp_path: Path, capsys):
    dest = tmp_path / "acme.pc"
    scaffold("acme.pc", ["routes"], dest, display_name="PC")
    assert cli(["plugin", "publish-check", str(dest)]) == 1
    out = capsys.readouterr().out
    assert "description is empty" in out and "tests/ has no test_*.py" not in out
    (dest / "narranexus-plugin.json").unlink()
    assert cli(["plugin", "publish-check", str(dest)]) == 1
    assert "missing" in capsys.readouterr().out


def test_cli_errors_are_messages_not_tracebacks(home: Path, capsys):
    assert cli(["plugin", "link", str(home / "nowhere")]) == 1
    assert "error:" in capsys.readouterr().err
    assert cli(["plugin", "enable", "acme.unknown"]) == 1
    assert cli(["plugin", "bisect", "start"]) == 1
    assert cli(["plugin", "rollback"]) == 1


def test_cli_toggles_builtins_through_overrides(home: Path, capsys):
    import json

    from narranexus.kernel.plugins.paths import registry_path

    assert cli(["plugin", "disable", "builtin.teams"]) == 0
    assert json.loads(registry_path().read_text())["builtin_overrides"]["builtin.teams"] == {"enabled": False}
    assert cli(["plugin", "enable", "builtin.teams"]) == 0
    assert "builtin.teams" not in json.loads(registry_path().read_text())["builtin_overrides"]
    assert cli(["plugin", "disable", "builtin.nexus_plugins_module"]) != 0  # protected
    assert cli(["plugin", "disable", "builtin.does_not_exist"]) != 0


def test_scaffold_writes_the_plugin_ci_workflow_and_list_shows_quality(tmp_path: Path, home: Path, capsys):
    import json

    dest = tmp_path / "acme.ci"
    files = scaffold("acme.ci", ["hook"], dest, display_name="CI")
    wf = dest / ".github" / "workflows" / "plugin-ci.yml"
    assert wf in files and "publish-check" in wf.read_text() and "softprops/action-gh-release" in wf.read_text()
    assert cli(["plugin", "link", str(dest)]) == 0
    capsys.readouterr()
    assert cli(["plugin", "list", "--json"]) == 0
    rows = json.loads(capsys.readouterr().out)["plugins"]
    assert rows[0]["id"] == "acme.ci" and rows[0]["quality"] == "bronze"


def test_index_repo_template_validates_and_matches_index_entry():
    import importlib.util
    import json

    root = Path(__file__).resolve().parents[2] / "examples" / "index-repo"
    spec = importlib.util.spec_from_file_location("validate_index", root / "validate_index.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.check(root) == []
    from narranexus.kernel.plugins.install.index import IndexEntry

    entries = [IndexEntry(**{**e, "tags": tuple(e.get("tags", ())), "kinds": tuple(e.get("kinds", ()))}) for e in json.loads((root / "index.json").read_text())]
    assert entries[0].id == "acme.hello_world"
    bad = {"id": "nodot", "repo": "x"}
    tmp = root.parent / "index-repo"
    assert mod.ID_RE.match(bad["id"]) is None and mod.REPO_RE.match(bad["repo"]) is None and tmp.is_dir()


def test_scaffold_merges_backend_activations_and_refuses_frontend_collisions(tmp_path):
    """Several backend kinds → one activate(ctx) that calls each kind's _activate_<kind>; two kinds that
    generate the same frontend file are refused instead of the second silently overwriting the first."""
    from narranexus.cli.scaffold import scaffold

    dest = tmp_path / "acme.multi"
    scaffold("acme.multi", ["routes", "table", "settings"], dest, display_name="Multi")
    init = (dest / "backend" / "__init__.py").read_text()
    assert init.count("def activate(") == 1
    for kind in ("routes", "table", "settings"):
        assert f"def _activate_{kind}(" in init and f"_activate_{kind}(ctx)" in init
    with pytest.raises(ValueError, match="both generate"):
        scaffold("acme.ui", ["ui_page", "ui_panel"], tmp_path / "acme.ui", display_name="UI")


_CONTRIBUTION_ID = re.compile(r'Contribution\(\s*"([^"]+)"')


def test_every_template_contribution_id_is_plugin_scoped():
    """Contribution ids are global within a slot, so a template that ships a
    generic id ("tools", "api", "sync", "team", "schema", "items") makes every
    second plugin scaffolded from it collide with the first and get isolated
    at boot — the first agent-written tool plugin hit exactly that against the
    hello-world sample (2026-09-08). Every id a template hands out carries the
    plugin placeholder."""
    offenders: list[str] = []
    for kind in KINDS:
        init = TEMPLATES_DIR / kind / "backend" / "__init__.py"
        if not init.is_file():
            continue
        for cid in _CONTRIBUTION_ID.findall(init.read_text()):
            if "__PLUGIN_PKG__" not in cid and "__PLUGIN_ID__" not in cid:
                offenders.append(f"{kind}: {cid!r}")
    assert offenders == [], offenders


def test_two_scaffolded_tool_plugins_boot_side_by_side(tmp_path: Path, home: Path):
    """The end-to-end form of the rule above on the real boot: two plugins
    from the tool template load together, neither is isolated, and both
    tool providers are visible."""
    from narranexus.hosts.boot import boot
    from narranexus.kernel.plugins.lifecycle import PluginRecord, RegistryStore
    from narranexus.kernel.plugins.paths import registry_path
    from narranexus.kernel.plugins.registries import Registries

    ids = ("acme.alpha", "acme.beta")
    store = RegistryStore(path=registry_path())
    for pid in ids:
        dest = tmp_path / pid
        scaffold(pid, ["tool"], dest, display_name=pid)
        store.register(pid, PluginRecord(path=str(dest), installed_version="0.1.0"))
    try:
        registries = Registries()
        report = boot("backend", registries=registries, cloud=False, host_version="1.19.0", store=store)
        assert report.isolated == {}, report.isolated
        names = {e.name for e in registries.registry_for("agent.capabilities.tools").entries() if e.owner in ids}
        assert names == {"acme_alpha", "acme_beta"}
    finally:
        for pid in ids:
            uninstall_synthetic_package(pid)
            plugin_finder().unregister_deps(pid)
