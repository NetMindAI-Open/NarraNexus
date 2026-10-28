"""
@file_name: plugins_boot.py
@author: Bin Liang
@date: 2026-09-03
@description: The backend process's plugin boot: wires the kernel boot sequence to this host's db, bus, settings store and activator.

``backend.main`` calls ``boot_backend_plugins()`` once in its lifespan,
before ``auto_migrate`` (plugin tables must exist even for inactive
plugins) and before routes are served; ``fire_startup()`` then activates
``onStartup`` plugins against the live process and ``mark_healthy`` clears
the boot marker. ``HOST_BUS`` / ``HOST_SERVICES`` are the backend's
process-wide event bus and service locator.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from loguru import logger

from narranexus.hosts.boot import BootReport, boot
from narranexus.kernel.deployment import is_cloud_mode
from narranexus.kernel.events.bus import EventBus
from narranexus.kernel.plugins.activation import Activator
from narranexus.kernel.plugins.compat import host_version
from narranexus.kernel.plugins.distribution import DistributionResolution, resolve_from_env
from narranexus.kernel.plugins.context import PluginContext, build_context
from narranexus.kernel.plugins.lifecycle import RegistryStore
from narranexus.kernel.plugins.manifest import Manifest
from narranexus.kernel.plugins.paths import registry_path
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.kernel.plugins.services import HOST_VERSION
from narranexus.kernel.settings import PluginSettings
from narranexus.kernel.settings.plugin_settings import MemorySettingsStore

from narranexus.platform.utils.db.schema_registry import register_table

HOST_BUS = EventBus()
# The backend process locator is the kernel registries' one (builtins expose
# their services there at import; user plugins through PluginContext.services).
HOST_SERVICES = KERNEL_REGISTRIES.services
_ACTIVATOR: Activator | None = None


def registry_store() -> RegistryStore:
    """Fresh each call so a relocated plugin home (tests) is honoured."""
    return RegistryStore(path=registry_path())


_HOST_DB: dict[str, Any] = {}


def set_host_db(db: Any) -> None:
    """The lifespan's async database client; plugin contexts and settings stores use it (a sync client
    cannot be built from inside the event loop)."""
    _HOST_DB["db"] = db


def host_db() -> Any:
    if "db" in _HOST_DB:
        return _HOST_DB["db"]
    from narranexus.platform.utils.db.db_factory import get_db_client_sync

    return get_db_client_sync()


def _settings_for(manifest: Manifest) -> PluginSettings:
    from narranexus.contracts.settings import SettingsSchema

    schema = SettingsSchema()
    if "backend.settings" in manifest.provides:
        try:
            for entry in KERNEL_REGISTRIES.registry_for("backend.settings").entries():
                if entry.owner == manifest.id:
                    schema = entry.factory()
                    break
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"[plugins] {manifest.id}: settings schema unavailable: {exc}")
    try:
        from narranexus.platform.utils.db.plugin_settings_store import DbSettingsStore

        store: Any = DbSettingsStore()  # no loop-bound client: the store drives its own loop + client
        # PluginSettings loads its rows eagerly — the DB round-trip happens HERE,
        # inside the guard: a transient DB error is a settings fallback, not a
        # plugin crash (two of those used to auto-disable a healthy plugin).
        return PluginSettings(manifest.id, schema, store=store)
    except Exception as exc:  # noqa: BLE001 — settings still work from env/defaults
        logger.warning(f"[plugins] {manifest.id}: settings store unavailable, using memory: {exc}")
        return PluginSettings(manifest.id, schema, store=MemorySettingsStore())


def _context_factory(manifest: Manifest) -> PluginContext:
    rec = registry_store().read().plugins.get(manifest.id)
    path = Path(rec.path) if rec else Path(".")
    return build_context(
        plugin_id=manifest.id,
        version=manifest.version,
        path=path,
        role="backend",
        host_version=host_version(),
        registries=KERNEL_REGISTRIES,
        provides=tuple(manifest.provides),
        settings=_settings_for(manifest),
        db_client=host_db(),
        bus=HOST_BUS,
        services=HOST_SERVICES.scoped(manifest.id),
    )


def activator() -> Activator:
    global _ACTIVATOR
    if _ACTIVATOR is None:
        _ACTIVATOR = Activator(
            context_factory=_context_factory,
            on_crash=lambda pid, err: registry_store().record_crash(pid, err),
        )
    return _ACTIVATOR


_DISTRIBUTION: dict[str, DistributionResolution | None] = {}


def distribution() -> DistributionResolution | None:
    """The distribution this backend runs (``NARRANEXUS_DIST``), resolved once; ``None`` = all builtins."""
    if "res" not in _DISTRIBUTION:
        _DISTRIBUTION["res"] = resolve_from_env(host_version=host_version())
    return _DISTRIBUTION["res"]


def write_runtime_bindings(res: DistributionResolution | None) -> Path | None:
    """Resolve the slot bindings once at startup (default < distribution < narranexus.toml < env), install them on
    the process registries and snapshot them — the shared ``platform.bindings_runtime`` does the work."""
    from narranexus.kernel.plugins.paths import plugin_home
    from narranexus.platform.bindings_runtime import SNAPSHOT_RELPATH, resolve_runtime_bindings

    resolved = resolve_runtime_bindings(res)
    return None if resolved is None else plugin_home() / SNAPSHOT_RELPATH


_LAST_REPORT: BootReport | None = None


def _cached_blocklist() -> dict[str, dict[str, str]] | None:
    """The index blocklist from the on-disk cache, no network: a plugin the index withdrew after it was installed is refused at boot (loader `blocked:`)."""
    try:
        from narranexus.kernel.plugins.install.index import Index
        from narranexus.kernel.plugins.paths import plugin_home

        return Index(cache_dir=plugin_home() / ".index-cache").cached_blocked()
    except Exception as exc:  # noqa: BLE001 — never fail a boot over the cache
        logger.debug(f"[plugins] cached blocklist unavailable: {exc}")
        return None


def boot_backend_plugins() -> BootReport:
    global _LAST_REPORT
    if HOST_SERVICES.try_require(HOST_VERSION) is None:
        HOST_SERVICES.expose(HOST_VERSION, host_version(), owner="builtin.kernel")
    if KERNEL_REGISTRIES.frozen:
        # Lifespan ran more than once in this process (tests build several
        # TestClients); the registries are already populated and frozen. Hand
        # back the FIRST boot's report so the factory page does not claim every
        # plugin is unloaded.
        if _LAST_REPORT is not None:
            return _LAST_REPORT
        return BootReport(role="backend")
    res = distribution()
    report = boot(
        "backend",
        registries=KERNEL_REGISTRIES,
        cloud=is_cloud_mode(),
        host_version=host_version(),
        activator=activator(),
        blocked_versions=_cached_blocklist(),
        register_table=register_table,
        store=registry_store(),
        distribution=res,
    )
    _LAST_REPORT = report
    try:
        write_runtime_bindings(res)
    except OSError as exc:
        logger.warning(f"[plugins] bindings snapshot not written: {exc}")
    return report


async def fire_startup() -> None:
    await activator().fire("onStartup")


__all__ = ["HOST_BUS", "HOST_SERVICES", "activator", "boot_backend_plugins", "distribution", "fire_startup", "host_db", "registry_store", "set_host_db", "write_runtime_bindings"]
