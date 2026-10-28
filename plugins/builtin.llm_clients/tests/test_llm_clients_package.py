"""
@file_name: test_llm_clients_package.py
@author: Bin Liang
@date: 2026-09-04
@description: Package contract of `builtin.llm_clients`: the manifest file equals the host's builtin manifest. Import isolation between builtin packages (no builtin may import another builtin's narranexus_plugins.* module) is enforced separately by the "builtin packages are independent (api facades excepted)" import-linter contract in pyproject.toml, not by this test.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_and_provides():
    from narranexus.kernel.plugins.loader import resolve_symbol

    on_disk = json.loads((ROOT / "narranexus-plugin.json").read_text())
    assert on_disk["id"] == "builtin.llm_clients" == ROOT.name  # the directory is the plugin id; the kernel reads this very file
    data = on_disk
    for refs in data["provides"].values():
        for ref in ([refs] if isinstance(refs, str) else refs):
            assert ref.startswith(("narranexus_plugins.llm_clients", "narranexus.platform.turn.pipeline")), ref
            resolve_symbol(ref)
