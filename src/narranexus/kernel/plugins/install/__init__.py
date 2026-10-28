"""
@file_name: __init__.py
@author: Bin Liang
@date: 2026-09-03
@description: Plugin installation — sources (GitHub Release / repo / local), integrity, dependency install, official index, and the ``Installer`` that ties them to ``registry.json``.

``Installer.install(source)`` is the one pipeline every entry point uses
(factory API, CLI, Nexus_Plugins_Module): fetch into a staging dir →
validate the manifest against this host → install pip dependencies
(wheels only, no build scripts, bounded time) → move into the plugin home
(or link in place) → register the record with asset hashes. Nothing is
written to ``registry.json`` until every step succeeded, and the registry
write itself snapshots the LKG first.
"""
from narranexus.kernel.plugins.install.installer import InstallError, Installer, InstallResult
from narranexus.kernel.plugins.install.sources import FetchResult, GitHubReleaseSource, GitHubRepoSource, LocalSource, Source

__all__ = [
    "FetchResult",
    "GitHubReleaseSource",
    "GitHubRepoSource",
    "InstallError",
    "InstallResult",
    "Installer",
    "LocalSource",
    "Source",
]
