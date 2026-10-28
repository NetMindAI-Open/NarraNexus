"""
@file_name: test_registry.py
@author: NarraNexus
@date: 2026-08-28
@description: Tests for the two-plugin registry — pins must track their
              single sources of truth, not be re-typed literals.
"""
from __future__ import annotations

from narranexus_plugins.frameworks_claude_code.cli_binary import PINNED_CLI_VERSION

from backend.integrations.plugins.registry import build_plugin_specs

# One build for the pin assertions below; the first test proves it is a
# snapshot of its moment by registering a framework into a private registries
# set and building again from that.
PLUGIN_SPECS = build_plugin_specs()


def test_a_framework_registered_later_appears_in_the_next_build():
    from narranexus.contracts.framework import FrameworkInstall, FrameworkMeta, InstallComponent
    from narranexus.kernel.plugins.builtins import slot_tree_with_builtins
    from narranexus.kernel.plugins.registries import Registries
    from narranexus.platform.agent_framework.loop.driver import FRAMEWORK_SLOT

    # A private registries set with the builtin plugins' slots declared (the
    # framework slot is builtin.turn's), untouched by the process-wide boot.
    private = Registries(slot_tree_with_builtins())
    install = FrameworkInstall(
        components=(InstallComponent(kind="pip", requirement="acme-loop==9.9.9"),),
        probe_package="acme_loop",
        user_version_source="pip_pkg",
        size_hint="~1 MB",
    )
    private.registry_for(FRAMEWORK_SLOT).register(
        "acme_loop", lambda: None, owner="acme.frameworks", meta={"framework": FrameworkMeta("acme_loop", "Acme", install=install)}
    )
    assert "acme_loop" not in PLUGIN_SPECS
    assert build_plugin_specs(private)["acme_loop"].components[0].requirement == "acme-loop==9.9.9"
    assert "acme_loop" not in build_plugin_specs()


def test_registry_has_exactly_claude_and_codex():
    assert set(PLUGIN_SPECS) == {"claude_code", "codex_cli"}


def test_claude_code_spec_components():
    spec = PLUGIN_SPECS["claude_code"]
    assert spec.probe_package == "claude_agent_sdk"
    assert spec.user_version_source == "npm_cli"
    kinds = [c.kind for c in spec.components]
    assert kinds == ["pip", "npm"]

    pip_component = spec.components[0]
    npm_component = spec.components[1]
    assert pip_component.requirement == "claude-agent-sdk==0.1.43"
    # The npm requirement's version must come FROM the CLI binary pin, not a
    # re-typed literal, so bumping PINNED_CLI_VERSION alone keeps them in sync.
    assert npm_component.requirement == f"@anthropic-ai/claude-code@{PINNED_CLI_VERSION}"


def test_codex_cli_spec_components():
    spec = PLUGIN_SPECS["codex_cli"]
    assert spec.probe_package == "openai_codex"
    assert spec.user_version_source == "pip_pkg"
    assert len(spec.components) == 1
    assert spec.components[0].kind == "pip"
    assert spec.components[0].requirement == "openai-codex==0.1.0b3"


def test_every_spec_has_a_size_hint():
    for spec in PLUGIN_SPECS.values():
        assert spec.size_hint
        assert isinstance(spec.size_hint, str)


def test_plugin_id_equals_framework_name_and_dict_key():
    """The pyenv install location is keyed on spec.id while framework_installed
    keys on the framework name (plugin_paths.plugin_pyenv(name)). Those must be
    the same string, or a plugin installs into pyenv/<id>/ while availability
    probes pyenv/<framework_name>/ and reports 'not installed' forever. This
    turns that docstring-only contract into a guard (same shape as
    test_plugins_extra_lockstep / test_claude_cli_pin)."""
    for key, spec in PLUGIN_SPECS.items():
        assert key == spec.id == spec.framework_name, (
            f"plugin key/id/framework_name diverge: key={key!r} id={spec.id!r} "
            f"framework_name={spec.framework_name!r} — install location and "
            f"availability probe would key on different dirs"
        )


def test_pip_pins_match_uv_lock():
    """I7 guard: the registry's exact pip pins (claude-agent-sdk==X /
    openai-codex==Y) are a hand-written copy of what uv.lock resolves for the
    plugins extra. If a `uv lock --upgrade` bumps one and the registry is not
    updated in step, cloud base and a local plugin install land on different
    versions while both report 'installed'. Parse uv.lock and assert they agree
    (same shape as test_claude_cli_pin.py / test_plugins_extra_lockstep.py)."""
    import re
    import tomllib
    from pathlib import Path

    repo = Path(__file__).resolve().parents[4]
    lock = tomllib.loads((repo / "uv.lock").read_text(encoding="utf-8"))
    locked = {pkg["name"]: pkg["version"] for pkg in lock.get("package", [])}

    # {distribution name -> pinned version} from every pip component.
    pinned: dict[str, str] = {}
    for spec in PLUGIN_SPECS.values():
        for comp in spec.components:
            if comp.kind != "pip":
                continue
            m = re.match(r"^([A-Za-z0-9_.\-]+)==(.+)$", comp.requirement)
            assert m, f"pip requirement not an exact pin: {comp.requirement!r}"
            pinned[m.group(1)] = m.group(2)

    assert pinned, "no pip pins found in PLUGIN_SPECS"
    # String equality is enough here: both sides are PEP 440 strings written by
    # the same toolchain (registry pin ← pyproject/uv.lock), not hand-typed, so
    # there is no `0.1.0b3` vs `0.1.0-beta3` normalization gap to bridge —
    # avoids depending on `packaging` (only a transitive dep).
    for name, ver in pinned.items():
        assert name in locked, f"{name} pinned in registry but absent from uv.lock"
        assert locked[name] == ver, (
            f"{name}: registry pins {ver} but uv.lock resolves {locked[name]} — "
            f"cloud base and local plugin install would diverge; bump the registry "
            f"pin in step with `uv lock`"
        )
