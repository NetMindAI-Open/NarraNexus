"""
@file_name: boot.py
@author: Bin Liang
@date: 2026-09-03
@description: Staged plugin boot for one host process (spec §9.2): builtins fail-fast, user plugins isolated, then freeze.

    stage1  builtins (declaration order)         a failure raises — a broken builtin must not boot
    stage2  user plugins (dependency order)      each isolated; total deadline; failures recorded on registry.json
            per plugin: synthetic package + deps finder → declarative contributions → tables registered
                        → activator learns its events → onStartup fired by the host when ready
    registries.freeze()

The boot marker is entered before stage 2 and exited by the host once it is
healthy (``BootReport.mark_healthy``). If a marker was left behind twice in
a row, ``registry.json`` flips to safe mode and this boot loads builtins
only. Everything the boot decides is returned in ``BootReport`` so the
factory page can show it; nothing is hidden in logs alone.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

from loguru import logger

from narranexus.contracts import PluginError
from narranexus.kernel.plugins.activation import Activator
from narranexus.kernel.plugins.importer import PluginImportTimeout, install_synthetic_package, plugin_finder
from narranexus.kernel.plugins.lifecycle import RegistryFile, BootMarker, RegistryError, RegistryStore
from narranexus.kernel.plugins.distribution import DistributionResolution
from narranexus.kernel.plugins.loader import Discovery, LoadReport, discover, load
from narranexus.kernel.plugins.manifest import Manifest
from narranexus.kernel.plugins.paths import deps_dir, registry_path
from narranexus.kernel.plugins.registries import Registries
from narranexus.contracts.distribution import is_builtin_id

Role = Literal["backend", "mcp", "workers"]
TableRegistrar = Callable[[Any, str], None]  # (TableSpec, owner)


@dataclass
class BootReport:
    role: Role
    safe_mode: bool = False
    safe_mode_reason: str = ""
    builtins: LoadReport | None = None
    users: LoadReport | None = None
    rejected: dict[str, str] = field(default_factory=dict)
    isolated: dict[str, str] = field(default_factory=dict)
    disabled_builtins: tuple[str, ...] = ()
    distribution: str | None = None  # id of the distribution that shaped this boot
    excluded_builtins: tuple[str, ...] = ()  # builtins the distribution left out
    # builtin id -> why its on-demand dependencies are unavailable (booted without it)
    deps_missing: dict[str, str] = field(default_factory=dict)
    activation_events: dict[str, tuple[str, ...]] = field(default_factory=dict)
    # user plugins the stage-2 deadline cut off before they loaded (state "slow",
    # never a crash: slow is not broken)
    slow: dict[str, str] = field(default_factory=dict)
    duration_ms: float = 0.0
    _marker: BootMarker | None = None
    _store: "RegistryStore | None" = field(default=None, repr=False)

    def mark_healthy(self) -> None:
        """Called by the host once it passed its own health probe: clears the
        boot-crash counter and snapshots ``registry.json`` as last-known-good —
        this is the only moment a registry state is proven bootable, so it is
        the only moment the rollback target moves."""
        if self._marker is not None:
            self._marker.exit()
        if self._store is not None:
            try:
                self._store.snapshot_lkg()
            except Exception as exc:  # noqa: BLE001 — never fail the host over the snapshot
                logger.warning(f"[plugins] last-known-good snapshot not written: {exc}")

    @property
    def user_plugin_ids(self) -> tuple[str, ...]:
        if self.users is None:
            return ()
        return tuple(p.plugin_id for p in self.users.loaded if not p.error)


# Per-plugin bound on a user plugin's imports during stage 2 (the stage deadline caps it further).
USER_IMPORT_TIMEOUT_S = 30.0


def _state_of(reason: str) -> str:
    return reason.split(":", 1)[0]


def _persist_rejections(store: RegistryStore, rejected: dict[str, str]) -> None:
    for pid, reason in rejected.items():
        try:
            store.transition(pid, _state_of(reason), error=reason)  # type: ignore[arg-type]
        except RegistryError as exc:
            logger.debug(f"[plugins] cannot record {pid} state: {exc}")


def boot(
    role: Role,
    *,
    registries: Registries,
    cloud: bool,
    host_version: str,
    activator: Activator | None = None,
    register_table: TableRegistrar | None = None,
    store: RegistryStore | None = None,
    blocked_versions: dict[str, dict[str, str]] | None = None,
    stage2_deadline_s: float = 120.0,
    distribution: "DistributionResolution | None" = None,
    inspect: bool = False,
) -> BootReport:
    """Boot the plugin platform for ``role`` into ``registries``.

    ``inspect=True`` is the read-only form for tools (``narranexus slots`` /
    ``bind`` / docs): discovery and registration happen exactly as in a real
    boot, but nothing is written back — no boot marker (three CLI runs used to
    push the app into SAFE MODE), no rejection/crash/state persistence.
    """
    started = time.perf_counter()
    report = BootReport(role=role)
    store = store or RegistryStore(path=registry_path())
    writable = not inspect
    if writable:
        report._store = store

    def _read_registry() -> "RegistryFile | None":
        # A corrupt registry.json must not stop the host (safe mode / LKG /
        # rollback exist for exactly that case and must be reachable).
        if not store.path.exists():
            return None
        try:
            return store.read()
        except RegistryError as exc:
            logger.error(f"[plugins] {store.path}: unreadable ({exc}); booting builtins only")
            return None

    # ---- safe-mode decision (local only, never in inspect mode)
    marker: BootMarker | None = None
    if not cloud and writable:
        marker = BootMarker(role)
        failures = marker.enter()
        current = _read_registry()
        already_safe = current is not None and current.safe_mode
        if marker.safe_mode_due and not already_safe:
            reason = f"{failures + 1} consecutive boots of {role} never reached health"
            store.set_safe_mode(True, reason=reason)
            logger.error(f"[plugins] entering SAFE MODE: {reason}")
        report._marker = marker

    found: Discovery = discover(
        cloud=cloud, user_registry_path=store.path, host_version=host_version, blocked_versions=blocked_versions
    )
    report.safe_mode = found.safe_mode
    if found.safe_mode:
        current = _read_registry()
        report.safe_mode_reason = current.safe_mode_reason if current is not None else ""
    report.rejected = dict(found.rejected)
    if report.rejected and writable:
        _persist_rejections(store, report.rejected)

    # ---- disabled builtins: undo their import-time registrations before anything is frozen
    report.disabled_builtins = tuple(found.disabled_builtins)
    for pid in found.disabled_builtins:
        removed = registries.remove_owner(pid)
        logger.info(f"[plugins] {pid}: disabled ({removed} contribution(s) removed)")

    # ---- on-demand builtin dependencies: probe / install; a builtin whose deps
    # are unavailable boots as if disabled (deps_missing, retry from the factory)
    from narranexus.kernel.plugins.install.builtin_deps import ensure_builtin_deps

    # ---- the distribution (spec section 19): its plugin set IS the stage-1 set —
    # builtins it leaves out lose their import-time registrations like a
    # disabled builtin, bundled plugins get their package and deps like a user
    # plugin but boot in stage 1 (fail-fast: they are the distribution's own code).
    stage1 = [m for m in found.manifests if m.is_builtin]
    if distribution is not None:
        distribution.raise_for_problems()
        report.distribution = distribution.spec.id
        selected = {m.id for m in distribution.manifests}
        left_out = tuple(m.id for m in stage1 if m.id not in selected)
        for pid in left_out:
            removed = registries.remove_owner(pid)
            logger.info(f"[plugins] {pid}: not in distribution {distribution.spec.id} ({removed} contribution(s) removed)")
        report.excluded_builtins = left_out
        disabled = set(found.disabled_builtins)
        stage1 = [m for m in distribution.manifests if m.id not in disabled]
        prepare_bundled_plugins(distribution, _read_registry(), skip=disabled)

    builtins = []
    for manifest in stage1:
        status = ensure_builtin_deps(manifest, cloud=cloud)
        if status.ok:
            builtins.append(manifest)
            continue
        report.deps_missing[manifest.id] = status.error or "dependencies missing"
        removed = registries.remove_owner(manifest.id)
        logger.warning(f"[plugins] {manifest.id}: deps_missing — booting without it ({removed} contribution(s) removed): {status.error}")

    # ---- stage 1: builtins (fail-fast inside load()); a distribution's bundled
    # plugin is its own code and fails the boot the same way
    report.builtins = load(registries, builtins, role=role)
    if distribution is not None:
        bundled_errors = [f"{pl.plugin_id}: {pl.error}" for pl in report.builtins.loaded if pl.error and pl.plugin_id in {p.id for p in distribution.picks if p.path}]
        if bundled_errors:
            raise PluginError(f"distribution {distribution.spec.id}: bundled plugin failed to load — " + "; ".join(bundled_errors))

    # ---- stage 2: user plugins, isolated
    users = [m for m in found.manifests if not m.is_builtin]
    if users and distribution is not None and not distribution.spec.runtime.user_plugins:
        logger.info(f"[plugins] distribution {distribution.spec.id} disallows runtime plugins: {len(users)} skipped")
        users = []
    loadable: list[Manifest] = []
    if users:
        # The deadline is a real bound on the whole stage — preparation AND the
        # imports load() performs (every user module goes through the kernel
        # importer's daemon-thread door with the time that is left) — so N
        # wedged plugins cannot drag the host past its health-check window.
        # A plugin the deadline catches is recorded "slow" (not crashed: slow
        # is not broken) and left unloaded this boot.
        deadline = time.perf_counter() + stage2_deadline_s
        finder = plugin_finder()
        registry_file = _read_registry()
        for manifest in users:
            if time.perf_counter() > deadline:
                report.slow[manifest.id] = f"slow: stage-2 deadline ({stage2_deadline_s:.0f}s) exceeded before this plugin loaded"
                continue
            try:
                _prepare_user_plugin(manifest, found.paths[manifest.id], finder, registry_file)
            except Exception as exc:  # noqa: BLE001 — isolate
                report.isolated[manifest.id] = f"{type(exc).__name__}: {exc}"
                logger.warning(f"[plugins] {manifest.id}: isolated before load: {exc}")
        loadable = [m for m in users if m.id not in report.isolated and m.id not in report.slow]
        report.users = load(registries, loadable, role=role, import_timeout_s=USER_IMPORT_TIMEOUT_S, deadline=deadline)
        for entry in report.users.loaded:
            if entry.slow:
                report.slow[entry.plugin_id] = entry.error or "slow"
            elif entry.error:
                report.isolated[entry.plugin_id] = entry.error
        loadable = [m for m in loadable if m.id not in report.slow]

    # ---- lifecycle for everything loaded this boot that is not a builtin: the
    # runtime (stage-2) plugins AND a distribution's bundled ones — bundled
    # plugins are stage-1 code but carry tables and activate(ctx) like any
    # other plugin (the shipped example distribution's acme.crm activates on
    # onStartup only because this loop sees it).
    bundled = [m for m in builtins if not m.is_builtin and not any(pl.plugin_id == m.id and pl.error for pl in report.builtins.loaded)]
    lifecycle = bundled + loadable
    if lifecycle:
        # tables are pure data: register them even when the plugin never activates.
        # (One slug, one owner — two ids sharing an ext_<slug>__ prefix cannot both
        # be here: the installer refuses the second id and the synthetic package
        # of the first holds the slug at prepare time.)
        if register_table is not None and "backend.tables" in registries.paths():
            for entry in registries.registry_for("backend.tables").entries():
                if is_builtin_id(entry.owner) or entry.owner in report.isolated:
                    continue
                try:
                    register_table(entry.factory(), entry.owner)
                except Exception as exc:  # noqa: BLE001
                    report.isolated[entry.owner] = f"table {entry.name!r}: {exc}"
                    # Isolation means NOTHING of it runs. A refused table used to
                    # leave the plugin's routes mounted, its hooks firing and its
                    # services resolvable while the report said "isolated" — a
                    # third-party plugin could get that state on purpose by
                    # shipping a colliding TableSpec. Withdraw all three the way
                    # a load failure does (registries.remove_owner covers
                    # registry entries, hooks.block and services.release_owner).
                    try:
                        withdrawn = registries.remove_owner(entry.owner)
                    except Exception as rollback_exc:  # noqa: BLE001 — the rollback must not fail the boot
                        withdrawn = 0
                        logger.error(f"[plugins] {entry.owner}: could not withdraw partial registrations: {rollback_exc}")
                    logger.warning(
                        f"[plugins] {entry.owner}: table {entry.name!r} refused ({withdrawn} registration(s) withdrawn): {exc}"
                    )
        if activator is not None:
            for manifest in lifecycle:
                if manifest.id in report.isolated:
                    continue
                report.activation_events[manifest.id] = activator.register(manifest)
    # Crash accounting runs AFTER every source of isolation (load errors,
    # table refusals) has been recorded — it used to run before the table
    # loop, so a plugin whose TableSpec was refused every boot never
    # reached the auto-disable threshold and replayed forever. Bundled
    # plugins are the distribution's own code: no crash counting for them
    # (their load errors already failed the boot above). Runs even when
    # nothing loaded: a stage deadline that caught every plugin still records them slow.
    if writable:
        runtime_ids = {m.id for m in users}
        for pid, err in report.isolated.items():
            if pid not in runtime_ids:
                continue
            try:
                store.record_crash(pid, err)
            except RegistryError:
                pass
        for pid, err in report.slow.items():
            try:
                store.transition(pid, "slow", error=err)
            except RegistryError:
                pass
        try:
            records = store.read().plugins  # one parse per boot, not one per plugin
        except RegistryError:
            records = {}
        for manifest in lifecycle:
            if manifest.id not in report.isolated:
                try:
                    record = records.get(manifest.id)
                    if record is not None and record.state in ("crashed", "slow"):
                        # A clean boot after a recorded crash/slow boot: leave the
                        # exceptional state (which only permits → registered) so
                        # the plugin does not stay "crashed" forever in the factory,
                        # and the crash budget counts consecutive crashes — reset it.
                        store.transition(manifest.id, "registered")
                        store.clear_crashes(manifest.id)
                    store.transition(manifest.id, "validated")
                    store.transition(manifest.id, "enabled")
                except RegistryError as exc:
                    logger.debug(f"[plugins] {manifest.id}: state not advanced: {exc}")

    registries.freeze()
    report.duration_ms = (time.perf_counter() - started) * 1000.0
    logger.info(
        f"[plugins] {role} boot: {len(builtins)} builtin, {len(report.user_plugin_ids)} user plugin(s) loaded, "
        f"{len(report.isolated)} isolated, {len(report.slow)} slow, {len(report.rejected)} rejected, safe_mode={report.safe_mode}, "
        f"{report.duration_ms:.0f} ms"
    )
    return report


def prepare_bundled_plugins(
    distribution: DistributionResolution,
    store: "RegistryStore | RegistryFile | None",
    *,
    skip: set[str] | frozenset[str] = frozenset(),
) -> tuple[str, ...]:
    """Give every bundled (path) plugin of the distribution its synthetic package and private
    deps so stage 1 can import it. Raises on a broken bundle: bundled code is the distribution's
    own. ``store`` may be the already-read registry (or ``None`` when it is unreadable — a
    corrupt registry.json must not keep a distribution from booting its own plugins)."""
    finder = plugin_finder()
    registry_file = store if isinstance(store, RegistryFile) or store is None else _try_read(store)
    prepared = []
    for pick in distribution.picks:
        if pick.path is None or pick.id in skip:
            continue
        _prepare_user_plugin(pick.manifest, pick.path, finder, registry_file)
        prepared.append(pick.id)
    return tuple(prepared)


def _try_read(store: RegistryStore) -> "RegistryFile | None":
    if not store.path.exists():
        return None
    try:
        return store.read()
    except RegistryError as exc:
        logger.error(f"[plugins] {store.path}: unreadable ({exc}); plugin records ignored")
        return None


def _prepare_user_plugin(manifest: Manifest, path: Path, finder: Any, registry: "RegistryFile | None") -> None:
    """Synthetic package + private deps for one user plugin (no code runs yet).

    ``registry`` is the registry.json read ONCE by the boot (``None`` when it
    is missing or unreadable): a plugin without a record installs in ``copy``
    mode, the default — never silently ``link``."""
    backend_pkg = manifest.backend.package if manifest.backend else "backend"
    backend_dir = path / backend_pkg
    if backend_dir.is_dir():
        install_synthetic_package(manifest.id, backend_dir)
    rec = registry.plugins.get(manifest.id) if registry is not None else None
    mode = rec.mode if rec else "copy"
    deps = deps_dir(manifest.id, mode=mode, plugin_path=path)
    if deps.is_dir():
        finder.register_deps(manifest.id, deps)


__all__ = ["BootReport", "Role", "boot"]
