"""
@file_name: test_frameworks_nexus_power_package.py
@author: Bin Liang
@date: 2026-09-04
@description: Package contract of `builtin.frameworks.nexus_power`: the manifest file equals the host's builtin manifest. Import isolation between builtin packages (no builtin may import another builtin's narranexus_plugins.* module) is enforced separately by the "builtin packages are independent (api facades excepted)" import-linter contract in pyproject.toml, not by this test.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_and_provides():
    from narranexus.kernel.plugins.loader import resolve_symbol

    on_disk = json.loads((ROOT / "narranexus-plugin.json").read_text())
    assert on_disk["id"] == "builtin.frameworks.nexus_power" == ROOT.name  # the directory is the plugin id; the kernel reads this very file
    data = on_disk
    for refs in data["provides"].values():
        for ref in ([refs] if isinstance(refs, str) else refs):
            # No exception any more: ``turn.pipeline``'s implementation moved into
            # builtin.turn's own package, so every builtin provides ref is inside
            # ``narranexus_plugins.`` (the shared assertion is in
            # tests/nx_kernel/platform/test_contribution_symbol_naming.py).
            assert ref.startswith("narranexus_plugins.frameworks_nexus_power"), ref
            resolve_symbol(ref)
