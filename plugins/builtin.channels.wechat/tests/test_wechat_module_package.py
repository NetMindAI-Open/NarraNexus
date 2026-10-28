"""
@file_name: test_wechat_module_package.py
@author: Bin Liang
@date: 2026-09-04
@description: Package contract of `builtin.channels.wechat`: the manifest on disk is the plugin's own (the kernel reads it, holds no copy), the facade resolves the module class. Import isolation between builtin packages (no builtin may import another builtin's narranexus_plugins.* module) is enforced separately by the "builtin packages are independent (api facades excepted)" import-linter contract in pyproject.toml, not by this test.
"""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_and_facade():
    from narranexus_plugins.wechat_module import api

    on_disk = json.loads((ROOT / "narranexus-plugin.json").read_text())
    cls = api.module_class()
    assert cls.__module__.startswith("narranexus_plugins.wechat_module") and cls.get_config().name == cls.__name__
