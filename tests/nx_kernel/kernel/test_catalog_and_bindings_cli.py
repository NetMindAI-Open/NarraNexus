"""
@file_name: test_catalog_and_bindings_cli.py
@author: Bin Liang
@date: 2026-09-07
@description: The slot catalog groups every slot by domain in display order with candidates and the live binding; the TOML template lists every slot; `narranexus bind` validates (unknown slot / provider / distribution-only / arity) and edits only the [bindings] table, `unbind` removes, and a written file resolves.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from narranexus.cli import bindings_cli
from narranexus.contracts import BindingConflict, UnknownEntry
from narranexus.kernel.plugins.bindings import BindingSource, Layer, resolve
from narranexus.kernel.plugins.builtins import slot_tree_with_builtins
from narranexus.kernel.plugins.catalog import domains, slot_catalog, toml_template
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution


def _regs():
    regs = Registries(slot_tree_with_builtins())
    regs.registry_for("prompt.assembler").register_contribution(Contribution("default", lambda: 1), owner="builtin.prompts")
    regs.registry_for("prompt.assembler").register_contribution(Contribution("brand", lambda: 2), owner="acme.brand")
    for n in ("security", "modules"):
        regs.registry_for("prompt.sections").register_contribution(Contribution(n, lambda: n), owner="builtin.prompts")
    return regs


def test_catalog_is_grouped_and_ordered_with_live_bindings():
    regs = _regs()
    cat = slot_catalog(regs)
    assert [g["domain"] for g in cat][:4] == ["kernel", "prompt", "turn", "model"]
    assert [g["domain"] for g in cat] == [d for d, _ in domains(regs.slots) if any(g["domain"] == d for g in cat)]
    assert all(g["title"] == regs.slots.get(g["domain"]).doc for g in cat)
    prompt = next(g for g in cat if g["domain"] == "prompt")
    asm = next(s for s in prompt["slots"] if s["path"] == "prompt.assembler")
    assert asm["candidates"] == ["acme.brand", "builtin.prompts"] and asm["bound"] == {"provider": "builtin.prompts", "layer": "DEFAULT"}
    regs.set_bindings(resolve(slot_tree_with_builtins(), [BindingSource(Layer.USER_CONFIG, {"prompt.assembler": "acme.brand"}, origin="t")]))
    asm = next(s for s in slot_catalog(regs, domain="prompt")[0]["slots"] if s["path"] == "prompt.assembler")
    assert asm["bound"]["provider"] == "acme.brand" and asm["bound"]["layer"] == "USER_CONFIG"
    assert len(slot_catalog(regs, domain="kernel")) == 1
    text = toml_template(cat)
    assert '"prompt.assembler" = "builtin.prompts"' in text and "kernel.auth" in text and text.startswith("# narranexus.toml")


def test_bind_validates_and_edits_only_the_bindings_table(tmp_path: Path):
    regs = _regs()
    cfg = tmp_path / "narranexus.toml"
    cfg.write_text('[other]\nkeep = 1\n')
    with pytest.raises(UnknownEntry, match="unknown slot"):
        bindings_cli.bind(regs, cfg, "prompt.nope", ["x"])
    with pytest.raises(BindingConflict, match="distribution-only"):
        bindings_cli.bind(regs, cfg, "kernel.auth", ["builtin.auth.netmind"])
    with pytest.raises(UnknownEntry, match="registered nothing"):
        bindings_cli.bind(regs, cfg, "prompt.assembler", ["acme.ghost"])
    with pytest.raises(BindingConflict, match="one-arity"):
        bindings_cli.bind(regs, cfg, "prompt.assembler", ["acme.brand", "builtin.prompts"])
    # a real owner's non-existent contribution, and a verb with no payload, are refused too
    with pytest.raises(UnknownEntry, match="registered nothing"):
        bindings_cli.bind(regs, cfg, "prompt.sections", ["builtin.prompts:nope"])
    with pytest.raises(UnknownEntry, match="registered nothing"):
        bindings_cli.bind(regs, cfg, "prompt.sections", ["-"])
    out = bindings_cli.bind(regs, cfg, "prompt.assembler", ["acme.brand"])
    assert out["bound"] == "acme.brand"
    out = bindings_cli.bind(regs, cfg, "prompt.sections", ["builtin.prompts:modules", "security"])
    assert out["bound"] == ["builtin.prompts:modules", "security"]
    text = cfg.read_text()
    assert "[other]" in text and "keep = 1" in text and '"prompt.assembler" = "acme.brand"' in text
    assert bindings_cli.read_bindings_table(cfg) == {"prompt.assembler": "acme.brand", "prompt.sections": ["builtin.prompts:modules", "security"]}
    from narranexus.kernel.plugins.bindings import parse_toml

    resolved = resolve(slot_tree_with_builtins(), [parse_toml(text, origin="t")])
    assert resolved.one["prompt.assembler"].provider == "acme.brand"
    assert bindings_cli.unbind(cfg, "prompt.assembler") and not bindings_cli.unbind(cfg, "prompt.assembler")
    assert "prompt.assembler" not in bindings_cli.read_bindings_table(cfg) and "keep = 1" in cfg.read_text()
