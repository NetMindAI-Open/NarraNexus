"""
@file_name: builtins.py
@author: Bin Liang
@date: 2026-09-03
@description: The host's own plugins, declared as manifests (D4: builtins are plugins too).

Explicit registration, not discovery (Python Packaging guide: explicit is
deterministic, greppable and fast): ``BUILTIN_PLUGINS`` is the ordered list of
(plugin id, package) pairs and the manifest of each is the plugin's own
``narranexus-plugin.json`` — read from the package (a wheel carries it) or the
source checkout. The kernel keeps no copy: a plugin's ``provides`` names its
``Contribution`` symbols and the loader registers exactly those at boot.
"""
from __future__ import annotations


import json
from functools import lru_cache

from loguru import logger
import importlib.util
from pathlib import Path
from typing import Any, Collection, Mapping

from narranexus.kernel.plugins.manifest import Manifest, parse_manifest
from narranexus.kernel.plugins.slots import SlotTree, build_kernel_slot_tree

# Stage-1 load order = this order. The id names the plugin directory
# (plugins/<id>/) and the package its manifest ships in
# (narranexus_plugins.<package>); the manifest itself is the JSON file — the
# kernel holds NO copy of it (charter: one knowledge, one home; adding a
# builtin does not touch a 338-line dict here any more).
BUILTIN_PLUGINS: tuple[tuple[str, str], ...] = (
    ("builtin.frameworks.nexus_power", "frameworks_nexus_power"),
    ("builtin.frameworks.claude_code", "frameworks_claude_code"),
    ("builtin.frameworks.codex_cli", "frameworks_codex_cli"),
    ("builtin.providers", "providers"),
    ("builtin.llm_clients", "llm_clients"),
    ("builtin.memory_kinds", "memory_kinds"),
    ("builtin.prompts", "prompts"),
    ("builtin.nexus_plugins_module", "nexus_plugins_module"),
    ("builtin.turn", "turn"),
    ("builtin.awareness", "awareness_module"),
    ("builtin.basic_info", "basic_info_module"),
    ("builtin.chat", "chat_module"),
    ("builtin.social_network", "social_network_module"),
    ("builtin.job", "job_module"),
    ("builtin.skills", "skill_module"),
    ("builtin.message_bus", "message_bus_module"),
    ("builtin.common_tools", "common_tools_module"),
    ("builtin.general_memory", "general_memory_module"),
    ("builtin.home_assistant", "home_assistant_module"),
    ("builtin.browser", "browser_module"),
    ("builtin.channels.lark", "lark_module"),
    ("builtin.channels.slack", "slack_module"),
    ("builtin.channels.telegram", "telegram_module"),
    ("builtin.channels.wechat", "wechat_module"),
    ("builtin.channels.narramessenger", "narramessenger_module"),
    ("builtin.channels.discord", "discord_module"),
    ("builtin.teams", "teams"),
    ("builtin.auth.local", "auth_local"),
    ("builtin.auth.netmind", "auth_netmind"),
    ("builtin.ui", "ui"),
)
MANIFEST_FILENAME = "narranexus-plugin.json"


def _manifest_path(plugin_id: str, package: str) -> Path:
    """The plugin's manifest: the copy packaged inside the wheel (hatch force-include) first, the source checkout second.

    The package is LOCATED, never imported. ``importlib.resources.files()``
    imports the package it is asked about, so merely reading 29 JSON files used
    to drag every builtin plugin's code (106 modules, ~1.2 s, message-source
    handlers registering themselves) into every process that touched the
    kernel — which also made a distribution's ``excludes`` cosmetic. A spec
    found by ``find_spec`` gives the package directory without executing it.
    """
    try:
        spec = importlib.util.find_spec(f"narranexus_plugins.{package}")
    except (ImportError, ValueError):  # a broken/partially-installed parent package
        spec = None
    if spec is not None:
        for location in spec.submodule_search_locations or ():
            packaged = Path(location) / MANIFEST_FILENAME
            if packaged.is_file():
                return packaged
    checkout = Path(__file__).resolve().parents[4] / "plugins" / plugin_id / MANIFEST_FILENAME
    if checkout.is_file():
        return checkout
    raise FileNotFoundError(f"{plugin_id}: {MANIFEST_FILENAME} not found in package narranexus_plugins.{package} nor at {checkout}")


@lru_cache(maxsize=1)
def _manifest_data_and_missing() -> tuple[tuple[dict[str, Any], ...], tuple[str, ...]]:
    """Read on FIRST USE, not at import (29 file reads must not tax every process that
    merely imports the kernel). A builtin whose package is not installed — a wheel-based
    distribution ships only its subset — is skipped and reported by ``missing_builtins()``
    rather than killing the process with FileNotFoundError; a distribution naming it then
    fails resolution with a message that says which plugin is absent."""
    out: list[dict[str, Any]] = []
    missing: list[str] = []
    for plugin_id, package in BUILTIN_PLUGINS:
        try:
            path = _manifest_path(plugin_id, package)
        except FileNotFoundError as exc:
            logger.warning(f"[plugins] builtin {plugin_id} is not installed in this engine: {exc}")
            missing.append(plugin_id)
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        if data.get("id") != plugin_id:
            raise ValueError(f"{plugin_id}: its manifest declares id {data.get('id')!r}")
        out.append(data)
    return tuple(out), tuple(missing)


def builtin_manifest_data() -> tuple[dict[str, Any], ...]:
    """The builtin manifests as parsed JSON, in load order (installed packages only)."""
    return _manifest_data_and_missing()[0]


def missing_builtins() -> tuple[str, ...]:
    """Builtin ids listed in ``BUILTIN_PLUGINS`` whose package this engine does not carry."""
    return _manifest_data_and_missing()[1]


def __getattr__(name: str) -> Any:
    # ``BUILTIN_MANIFEST_DATA`` stays importable by name; resolved lazily.
    if name == "BUILTIN_MANIFEST_DATA":
        return builtin_manifest_data()
    raise AttributeError(name)


def load_builtins(registries: Any, role: str = "backend", *, distribution: Any = None) -> Any:
    """Register every builtin's contributions for ``role`` into ``registries`` — the
    one way a process (or a test) gets the builtins WITHOUT the host boot's
    discovery, marker and write-back. What ``hosts.boot`` does in stage 1;
    private registries in tests use it directly. Honours a distribution's
    picks when one is given. Does not freeze.
    """
    from narranexus.kernel.plugins.loader import load

    manifests = list(builtin_manifests())
    if distribution is not None:
        selected = {m.id for m in distribution.manifests}
        manifests = [m for m in manifests if m.id in selected]
    return load(registries, manifests, role=role)  # type: ignore[arg-type]



def build_builtin_manifests(tree: SlotTree) -> tuple[Manifest, ...]:
    """Validate the builtin manifest data against ``tree`` (uncached).

    Pass 1 declares every builtin's own slots into ``tree`` (a plugin's
    ``declares`` need no tree to parse), so pass 2 can check each manifest's
    ``provides`` against the slots the OTHER builtins declare —
    ``builtin.frameworks.*`` provide into ``turn.pipeline.act.framework``,
    which ``builtin.turn`` declares, whatever their relative order.
    """
    data_list = builtin_manifest_data()
    tree.declare_all(slot for data in data_list for slot in Manifest.model_validate(data).declared_slots())
    return tuple(parse_manifest(data, tree=tree, allow_builtin=True) for data in data_list)


def slot_tree_with_builtins() -> SlotTree:
    """The kernel tree plus every slot a builtin plugin declares (e.g. ``builtin.turn``'s stage slots).

    User-plugin validation (install, discover, publish-check, self-extension)
    must see these: a third-party Recall strategy provides into
    ``turn.pipeline.recall``, which only exists once ``builtin.turn`` declared it.
    """
    tree = build_kernel_slot_tree()
    tree.declare_all(slot for manifest in builtin_manifests() for slot in manifest.declared_slots())
    return tree


@lru_cache(maxsize=1)
def builtin_manifests() -> tuple[Manifest, ...]:
    """Validated builtin manifests against the kernel tree (cached; the data is a constant)."""
    return build_builtin_manifests(build_kernel_slot_tree())


def excluded_builtins_of_running_distribution(environ: Mapping[str, str] | None = None) -> frozenset[str]:
    """Builtins the distribution this process runs (``NARRANEXUS_DIST``) leaves out; empty when none is configured.

    For processes WITHOUT a boot report (the ``narranexus`` CLI). A running backend already holds the
    same answer as ``BootReport.excluded_builtins`` and passes that to ``check_builtin_toggle`` instead of
    resolving again. A broken distribution raises ``DistributionError``: guessing "nothing excluded" there
    would green-light exactly the toggles the distribution forbids.
    """
    from narranexus.kernel.plugins.distribution import resolve_from_env

    res = resolve_from_env(environ)
    return frozenset(res.excluded) if res is not None else frozenset()


def check_builtin_toggle(plugin_id: str, enabled: bool, *, excluded: Collection[str] | None = None) -> Manifest:
    """The ONE rule set for switching a builtin on/off through ``registry.json`` ``builtin_overrides``.

    Raises ``KeyError`` for an unknown id, ``RegistryError`` for a builtin the running distribution
    excludes (it is never loaded, so an override would be a silent no-op reported as done) or for
    disabling a protected one. The factory API and the CLI both call this; add a new toggle rule here,
    not in either caller. ``excluded=None`` resolves the distribution from the environment.
    """
    from narranexus.kernel.plugins.lifecycle import RegistryError

    manifest = next((m for m in builtin_manifests() if m.id == plugin_id), None)
    if manifest is None:
        raise KeyError(f"{plugin_id} is not a builtin plugin")
    refuse_if_excluded(plugin_id, excluded)
    if manifest.protected and not enabled:
        raise RegistryError(f"{plugin_id} is protected and cannot be disabled")
    return manifest


def refuse_if_excluded(plugin_id: str, excluded: Collection[str] | None = None) -> None:
    """Raise ``RegistryError`` when ``plugin_id`` is left out by the running distribution (``None`` = resolve from env)."""
    from narranexus.kernel.plugins.lifecycle import RegistryError

    left_out = excluded_builtins_of_running_distribution() if excluded is None else excluded
    if plugin_id in left_out:
        raise RegistryError(f"{plugin_id} is excluded by this distribution and cannot be changed here")


__all__ = ["BUILTIN_PLUGINS", "MANIFEST_FILENAME", "build_builtin_manifests", "builtin_manifest_data", "builtin_manifests", "check_builtin_toggle", "excluded_builtins_of_running_distribution", "load_builtins", "missing_builtins", "refuse_if_excluded", "slot_tree_with_builtins"]
