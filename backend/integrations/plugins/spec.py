"""
@file_name: spec.py
@author: NarraNexus
@date: 2026-08-28
@description: Immutable data contract describing what a coding-agent
              framework plugin is made of and how to tell it apart from
              "not installed".

The atomic install unit is the contract's ``InstallComponent`` (one pip
wheel or one npm package, its pinned version inside ``requirement`` — the
installer layer never invents a version); it is re-exported here for the
installers. PluginSpec groups the components
that together make one user-facing plugin, plus the metadata needed to
report status (probe_package, user_version_source, size_hint).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from narranexus.contracts.framework import InstallComponent


@dataclass(frozen=True)
class PluginSpec:
    """A user-facing plugin: one or more InstallComponents plus status metadata.

    ``probe_package`` is the python import name that decides "is this
    framework's code present" (``FrameworkInstall.probe_package``);
    ``login_marker`` is the framework's ``(subdir, filename)`` under the home
    directory whose presence means its CLI is logged in. ``user_version_source``
    picks which single component's detected version is surfaced to the user
    when a plugin has more than one component (Claude Code has both a pip
    wheel and an npm CLI; the CLI's version is the one users recognize).
    """

    id: str
    display_name: str
    framework_name: str
    components: tuple[InstallComponent, ...]
    probe_package: str
    user_version_source: Literal["npm_cli", "pip_pkg"]
    size_hint: str
    login_marker: tuple[str, str] | None = None

__all__ = ["InstallComponent", "PluginSpec"]
