"""
@file_name: test_plugin_contributions.py
@author: Bin Liang
@date: 2026-09-03
@description: The registry read-side helpers isolate broken entries, keep declaration order and shape values for their consumers.
"""
from __future__ import annotations

from pathlib import Path

from narranexus.contracts.bundle import BundleSpec
from narranexus.contracts.mcp_server import McpServerSpec
from narranexus.contracts.skill import SkillSpec
from narranexus.contracts.tool import ToolSpec
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.utils import plugin_contributions as pc


def _reg(slot, *items):
    registries = Registries()
    r = registries.registry_for(slot)
    for owner, name, factory in items:
        r.register_contribution(Contribution(name, factory), owner=owner)
    return registries


def test_mcp_servers_shape_first_wins_and_broken_entries_are_skipped():
    registries = _reg(
        pc.MCP_SERVERS_SLOT,
        ("acme.a", "w", lambda: McpServerSpec("weather", "streamable_http", url="https://w/mcp", headers={"A": "1"})),
        ("acme.b", "w2", lambda: McpServerSpec("weather", "sse", url="https://dup")),
        ("acme.c", "local", lambda: McpServerSpec("local", "stdio", command="uvx", args=("x",))),
        ("acme.d", "boom", lambda: (_ for _ in ()).throw(RuntimeError("no"))),
        ("acme.e", "wrong", lambda: object()),
    )
    out = pc.plugin_mcp_servers(registries)
    assert out["weather"] == {"url": "https://w/mcp", "headers": {"A": "1"}}
    assert out["local"]["command"] == "uvx" and out["local"]["args"] == ["x"]
    # A stdio child must be able to import nxplugins.<id>: the config carries
    # the bootstrap PYTHONPATH and the plugin home (kernel.plugins.subprocess_env).
    from narranexus.kernel.plugins.paths import ENV_PLUGIN_HOME, plugin_home
    from narranexus.kernel.plugins.subprocess_env import bootstrap_dir

    assert out["local"]["env"]["PYTHONPATH"] == str(bootstrap_dir())
    assert out["local"]["env"][ENV_PLUGIN_HOME] == str(plugin_home())
    assert set(out) == {"weather", "local"}


def test_tools_follow_provider_order_and_dedupe_names():
    class P:
        def __init__(self, *tools):
            self._t = tools

        def list_tools(self):
            return self._t

    registries = _reg(
        pc.TOOLS_SLOT,
        ("acme.a", "p1", lambda: P(ToolSpec("b", "B"), ToolSpec("a", "A", always_visible=True))),
        ("acme.b", "p2", lambda: P(ToolSpec("a", "dup"), ToolSpec("c", "C"))),
        ("acme.c", "notprovider", lambda: object()),
    )
    assert [t.name for t in pc.plugin_tools(registries)] == ["b", "a", "c"]
    assert pc.plugin_tools(registries)[1].always_visible is True


def test_skills_and_bundles_require_existing_files(tmp_path: Path):
    good = tmp_path / "good"
    good.mkdir()
    (good / "SKILL.md").write_text("---\nname: good\n---\n")
    missing = tmp_path / "missing"
    bundle = tmp_path / "t.nxbundle"
    bundle.write_bytes(b"x")
    registries = _reg(
        pc.SKILLS_SLOT,
        ("acme.a", "good", lambda: SkillSpec(good)),
        ("acme.a", "missing", lambda: SkillSpec(missing)),
    )
    assert [s.path for _, s in pc.plugin_skills(registries)] == [good]
    registries.registry_for(pc.BUNDLES_SLOT).register_contribution(
        Contribution("t", lambda: BundleSpec("acme.t", bundle, "a" * 64)), owner="acme.a"
    )
    registries.registry_for(pc.BUNDLES_SLOT).register_contribution(
        Contribution("gone", lambda: BundleSpec("acme.gone", tmp_path / "gone.nxbundle", "b" * 64)), owner="acme.a"
    )
    assert [b.id for _, b in pc.plugin_bundles(registries)] == ["acme.t"]
