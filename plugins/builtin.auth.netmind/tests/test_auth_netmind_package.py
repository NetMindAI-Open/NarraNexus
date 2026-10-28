"""
@file_name: test_auth_netmind_package.py
@author: Bin Liang
@date: 2026-09-04
@description: Package contract of `builtin.auth.netmind`: the manifest equals the host's builtin manifest, is distribution-only, and its provider answers the `kernel.auth` contract.
"""
from __future__ import annotations

import json
from pathlib import Path

from narranexus.contracts.services import AuthProvider

ROOT = Path(__file__).resolve().parents[1]


def test_manifest_matches_host_and_is_distribution_only():

    on_disk = json.loads((ROOT / "narranexus-plugin.json").read_text())
    assert on_disk["distributionOnly"] is True and on_disk["provides"] == {"kernel.auth": "narranexus_plugins.auth_netmind.provider:CONTRIBUTION"}


def test_contribution_builds_an_auth_provider():
    from narranexus_plugins.auth_netmind.provider import CONTRIBUTION

    provider = CONTRIBUTION.factory()
    assert isinstance(provider, AuthProvider) and provider.id == "builtin.auth.netmind"
