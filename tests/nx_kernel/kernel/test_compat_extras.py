"""
@file_name: test_compat_extras.py
@author: Bin Liang
@date: 2026-09-03
@description: versions.json selection, blocked_versions lookup and the host version source.
"""
from __future__ import annotations

from narranexus.kernel.plugins.compat import Version, blocked_reason, host_version, select_version


def test_select_version_picks_newest_that_fits():
    versions = {"1.0.0": "1.10.0", "1.1.0": "1.15.0", "2.0.0": "1.19.0"}
    assert select_version(versions, "1.18.0") == "1.1.0"
    assert select_version(versions, "1.19.0") == "2.0.0"
    assert select_version(versions, "1.0.0") is None


def test_blocked_reason():
    blocked = {"acme.x": {"below": "1.2.0", "reason": "leaks keys"}}
    assert blocked_reason(blocked, "acme.x", "1.1.9") == "leaks keys"
    assert blocked_reason(blocked, "acme.x", "1.2.0") is None
    assert blocked_reason(blocked, "acme.y", "0.0.1") is None
    assert blocked_reason({"acme.x": {"below": "1.0.0"}}, "acme.x", "0.1.0") == "versions below 1.0.0 are blocked"


def test_host_version_is_a_semver():
    Version.parse(host_version())
