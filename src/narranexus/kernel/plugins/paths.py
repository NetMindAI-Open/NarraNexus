"""
@file_name: paths.py
@author: Bin Liang
@date: 2026-09-03
@description: The on-disk layout of user plugins: one tree under ``~/.narranexus/plugins``.

    ~/.narranexus/plugins/                 plugin_home()  (NARRANEXUS_PLUGIN_HOME overrides)
    ├── registry.json                      the single source of truth for user plugins
    ├── registry.lkg.json                  last-known-good copy, written before every change
    ├── .booting-<role>                    boot marker; two leftovers in a row → safe mode
    ├── nodejs/  pyenv/                    the framework installers' trees (legacy plugin_paths)
    ├── <publisher>.<name>/                a plugin installed in ``copy`` mode
    │   ├── narranexus-plugin.json  backend/  frontend/dist/plugin.js
    │   └── pyenv/                         its pip dependencies (copy mode)
    └── ...
    ~/.narranexus/plugin-deps/<id>/        pip dependencies of a ``link``-mode plugin

Plugin ids always contain a dot (``<publisher>.<name>``), so a plugin directory
can never collide with ``nodejs`` / ``pyenv`` / the registry files. This module
is pure path arithmetic; nothing here touches the filesystem except
``ensure_home``.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

ENV_PLUGIN_HOME = "NARRANEXUS_PLUGIN_HOME"
MANIFEST_FILENAME = "narranexus-plugin.json"
REGISTRY_FILENAME = "registry.json"
LKG_FILENAME = "registry.lkg.json"
VERSIONS_FILENAME = "versions.json"

Mode = Literal["copy", "link"]


def plugin_home(environ: os._Environ[str] | dict[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    override = (env.get(ENV_PLUGIN_HOME) or "").strip()
    if override:
        return Path(override).expanduser()
    return Path.home() / ".narranexus" / "plugins"


def ensure_home() -> Path:
    home = plugin_home()
    home.mkdir(parents=True, exist_ok=True)
    return home


def plugin_dir(plugin_id: str) -> Path:
    """Where a ``copy``-mode plugin lives (a ``link``-mode plugin lives at its own path)."""
    return plugin_home() / plugin_id


def deps_dir(plugin_id: str, *, mode: Mode, plugin_path: Path | None = None) -> Path:
    """The pip ``--target`` directory for one plugin's dependencies."""
    if mode == "copy":
        return (plugin_path or plugin_dir(plugin_id)) / "pyenv"
    return plugin_home().parent / "plugin-deps" / plugin_id


def frontend_dist_dir(plugin_path: Path) -> Path:
    return plugin_path / "frontend" / "dist"


def manifest_path(plugin_path: Path) -> Path:
    return plugin_path / MANIFEST_FILENAME


def registry_path() -> Path:
    return plugin_home() / REGISTRY_FILENAME


def lkg_path() -> Path:
    return plugin_home() / LKG_FILENAME


def boot_marker_path(role: str) -> Path:
    return plugin_home() / f".booting-{role}"


__all__ = [
    "ENV_PLUGIN_HOME",
    "LKG_FILENAME",
    "MANIFEST_FILENAME",
    "Mode",
    "REGISTRY_FILENAME",
    "VERSIONS_FILENAME",
    "boot_marker_path",
    "deps_dir",
    "ensure_home",
    "frontend_dist_dir",
    "lkg_path",
    "manifest_path",
    "plugin_dir",
    "plugin_home",
    "registry_path",
]
