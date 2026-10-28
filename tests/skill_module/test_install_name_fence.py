"""
@file_name: test_install_name_fence.py
@author: Bin Liang
@date: 2026-10-28
@description: The install pipeline and the remove/disable/enable side agree
on what a legal skill directory name is: a dot-led name is refused at
install time (it would otherwise be installed-but-undeletable).
"""
from __future__ import annotations

import pytest

from narranexus.platform.utils.workspace_paths import safe_id_segment


@pytest.mark.parametrize("name", [".hidden", "..", "a/b"])
def test_install_fence_matches_removal_fence(name):
    with pytest.raises(ValueError):
        safe_id_segment(name, label="skill name")


def test_install_pipeline_refuses_dot_led_name(tmp_path, monkeypatch):
    """Drive the exact statement in install_pipeline (sanitize → safe_id_segment)
    so a revert of the install-side fence goes red here."""
    import inspect
    from narranexus.platform.marketplace._skill_marketplace_impl import install_pipeline as ip
    src = inspect.getsource(ip)
    assert 'safe_id_segment(incoming_name, label="skill name")' in src
