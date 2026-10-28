"""
@file_name: test_slots_and_bind_cli.py
@author: Bin Liang
@date: 2026-09-07
@description: `narranexus slots` and `narranexus bind` through the real CLI: read-only tools leave the plugin home byte-identical, and bind writes narranexus.toml.

``booted_registries()`` is the only caller of ``boot(..., inspect=True)`` and
the CLI is its only caller, but no test ever went through ``cli([...])``: the
existing binding tests hand a ``Registries`` straight to ``bindings_cli.bind``,
which skips the boot entirely. The named regression — three ``narranexus slots``
runs pushing the app into SAFE MODE, because each run left a boot marker behind
— is a property of the COMMAND, so only a command-level test can see it.
"""
from __future__ import annotations

from pathlib import Path

from narranexus.cli.main import main as cli


def _snapshot(home: Path) -> dict[str, bytes]:
    return {str(p.relative_to(home)): p.read_bytes() for p in sorted(home.rglob("*")) if p.is_file()}


def test_slots_is_read_only_no_matter_how_often_it_runs(tmp_path: Path, monkeypatch, capsys):
    """Three runs, byte-for-byte comparison of the WHOLE home — not two named
    paths. A boot marker, a crash count, a state transition or a written
    ``registry.lkg.json`` all show up here."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("NARRANEXUS_PLUGIN_HOME", str(home))

    assert cli(["slots"]) == 0
    first = capsys.readouterr().out
    assert "prompt.assembler" in first and "turn.pipeline.act.framework" in first
    before = _snapshot(home)

    for _ in range(3):
        assert cli(["slots", "--domain", "prompt"]) == 0
    assert _snapshot(home) == before
    assert not (home / ".booting-backend").exists()
    assert not (home / "registry.lkg.json").exists()
    # and the app is not in safe mode afterwards
    registry = home / "registry.json"
    assert not registry.exists() or "safe_mode\": true" not in registry.read_text()


def test_slots_json_and_toml_template_render_the_same_catalog(tmp_path: Path, monkeypatch, capsys):
    monkeypatch.setenv("NARRANEXUS_PLUGIN_HOME", str(tmp_path))
    assert cli(["slots", "--json"]) == 0
    payload = capsys.readouterr().out
    assert '"prompt.assembler"' in payload
    assert cli(["slots", "--toml-template"]) == 0
    template = capsys.readouterr().out
    assert template.startswith("# narranexus.toml") and '"prompt.assembler"' in template


def test_bind_through_the_cli_writes_the_bindings_table_and_rejects_a_bad_slot(tmp_path: Path, monkeypatch, capsys):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("NARRANEXUS_PLUGIN_HOME", str(home))

    assert cli(["bind", "prompt.assembler", "builtin.prompts"]) == 0
    config = home / "narranexus.toml"
    assert '"prompt.assembler" = "builtin.prompts"' in config.read_text()

    # An unknown slot is a non-zero exit with a message, never a traceback and
    # never a half-written file.
    before = config.read_text()
    assert cli(["bind", "prompt.nope", "builtin.prompts"]) == 1
    assert "unknown slot" in capsys.readouterr().err
    assert config.read_text() == before

    assert cli(["unbind", "prompt.assembler"]) == 0
    assert '"prompt.assembler"' not in config.read_text()
