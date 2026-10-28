"""
@file_name: loader.py
@author: Bin Liang
@date: 2026-09-03
@description: Discover manifests and load their contributions into the registries, per host role.

Batch 0 scope: builtin plugins only. ``discover`` returns the builtin manifests
and, on the cloud deployment, nothing else — ever (D1: the cloud runs zero
user plugins; the user registry path is ignored there by construction, not by
configuration). User-plugin discovery from ``registry.json`` arrives with the
plugin factory in batch 2 and plugs into the same ``load``.

``load`` is deliberately small: for each manifest whose ``hosts`` include the
current role, resolve every ``provides`` symbol (``module.path:attr``) and
register the ``Contribution``(s) it names into the registry for that slot.
Import happens here and only here, so a plugin's import cost is paid once and
attributed to it in the ``LoadReport``. A builtin that fails to import raises —
a broken builtin must stop the process (today's behavior) — while a user plugin
is isolated (batch 2 wires the crash counter).
"""
from __future__ import annotations

import importlib
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from loguru import logger

from narranexus.contracts import ManifestError, PluginError
from narranexus.kernel.plugins.manifest import Host, Manifest, missing_api_kinds
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.slots import Slot
from narranexus.kernel.plugins.hooks import HookImplSpec
from narranexus.kernel.plugins.registry import Contribution

HOOKS_SLOT = "backend.hooks"
SERVICES_SLOT = "backend.services"


@dataclass(frozen=True)
class PluginLoad:
    plugin_id: str
    version: str
    slots: tuple[str, ...]
    entries: int
    duration_ms: float
    error: str | None = None
    slow: bool = False  # not loaded because the import deadline / stage deadline ran out (not broken)


@dataclass
class LoadReport:
    role: Host
    loaded: list[PluginLoad] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # not for this host
    # Non-fatal manifest findings surfaced to the factory page, one line each:
    # today the deprecation window of docs/API_POLICY.md section 4 (a
    # third-party manifest whose ``api`` does not version every contract kind
    # it fills). A builtin never reaches here — for builtins the same finding
    # is a ManifestError at parse time.
    warnings: list[str] = field(default_factory=list)

    @property
    def errors(self) -> list[PluginLoad]:
        return [p for p in self.loaded if p.error]


@dataclass
class Discovery:
    manifests: list[Manifest]
    paths: dict[str, Path] = field(default_factory=dict)  # user plugin id -> plugin dir
    rejected: dict[str, str] = field(default_factory=dict)  # user plugin id -> reason (state name: detail)
    safe_mode: bool = False
    disabled_builtins: tuple[str, ...] = ()  # registry.json builtin_overrides[id].enabled == False


def discover(
    *,
    cloud: bool,
    user_registry_path: Path | None = None,
    host_version: str | None = None,
    blocked_versions: Mapping[str, Mapping[str, str]] | None = None,
) -> Discovery:
    """Builtin manifests, plus (never on cloud, never in safe mode) the enabled user plugins.

    Each enabled record in ``registry.json`` is validated: the plugin dir
    must exist (else ``missing``), its manifest must parse against the tree
    and the host version (else ``incompatible``), its id must match the
    record, and it must not be on the blocklist (else ``blocked``). Rejected
    plugins are reported, not loaded; the caller persists their state.
    """
    from narranexus.kernel.plugins.builtins import builtin_manifests
    from narranexus.kernel.plugins.compat import blocked_reason
    from narranexus.kernel.plugins.lifecycle import RegistryStore
    from narranexus.kernel.plugins.manifest import load_manifest
    from narranexus.kernel.plugins.paths import manifest_path, registry_path
    from narranexus.kernel.plugins.builtins import slot_tree_with_builtins

    found = Discovery(manifests=list(builtin_manifests()))
    if cloud:
        logger.info("[plugins] cloud deployment: user plugin registry ignored (fail-closed)")
        return found
    store = RegistryStore(path=user_registry_path or registry_path())
    if not store.path.exists():
        return found
    try:
        reg = store.read()
    except Exception as exc:  # noqa: BLE001 — a corrupt registry must not stop the host; builtins still boot
        logger.error(f"[plugins] {store.path}: unreadable, loading builtins only: {exc}")
        return found
    # A builtin switched off in registry.json (builtin_overrides) is dropped
    # from the load set; the boot also removes its import-time registrations.
    disabled = tuple(sorted(pid for pid, o in reg.builtin_overrides.items() if o.get("enabled") is False))
    protected = {m.id for m in found.manifests if m.protected}
    disabled = tuple(pid for pid in disabled if pid not in protected)
    if disabled:
        found.manifests = [m for m in found.manifests if m.id not in disabled]
        found.disabled_builtins = disabled
        logger.info(f"[plugins] builtins disabled by override: {list(disabled)}")
    if reg.safe_mode:
        logger.warning(f"[plugins] SAFE MODE: user plugins skipped ({reg.safe_mode_reason or 'no reason recorded'})")
        found.safe_mode = True
        return found
    tree = slot_tree_with_builtins()
    for pid, rec in sorted(reg.plugins.items()):
        if not rec.enabled:
            continue
        plugin_path = Path(rec.path)
        mpath = manifest_path(plugin_path)
        if not mpath.is_file():
            found.rejected[pid] = f"missing: {mpath} not found"
            continue
        try:
            manifest = load_manifest(mpath, tree=tree, host_version=host_version)
        except ManifestError as exc:
            found.rejected[pid] = f"incompatible: {exc}"
            continue
        if manifest.id != pid:
            found.rejected[pid] = f"incompatible: manifest id {manifest.id!r} does not match registry entry {pid!r}"
            continue
        if manifest.distribution_only:
            found.rejected[pid] = "incompatible: distribution-only plugin (authProviders and other kernel slots) cannot be installed at runtime; build it into a distribution"
            continue
        reason = blocked_reason(blocked_versions or {}, pid, manifest.version)
        if reason:
            found.rejected[pid] = f"blocked: {reason}"
            continue
        found.manifests.append(manifest)
        found.paths[pid] = plugin_path
    return found


Importer = Callable[[str], Any]


def resolve_symbol(spec: str, importer: Importer | None = None) -> Any:
    """``module.path:Symbol`` → the object; ``importer`` replaces the plain import (the boot passes a bounded one for user plugins)."""
    module_path, _, attr = spec.partition(":")
    module = (importer or importlib.import_module)(module_path)
    try:
        return getattr(module, attr)
    except AttributeError:
        raise PluginError(f"{spec}: module {module_path!r} has no attribute {attr!r}") from None


def _as_contributions(value: Any, spec: str) -> list[Contribution[Any]]:
    if isinstance(value, Contribution):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        items = list(value)
        if all(isinstance(i, Contribution) for i in items):
            return items
    raise PluginError(f"{spec}: expected a Contribution or an iterable of Contributions, got {type(value).__name__}")


def _as_hook_impls(value: Any, spec: str) -> list[HookImplSpec]:
    if isinstance(value, HookImplSpec):
        return [value]
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        items = list(value)
        if all(isinstance(i, HookImplSpec) for i in items):
            return items
    raise PluginError(f"{spec}: expected a HookImplSpec or an iterable of them (use @hookimpl), got {type(value).__name__}")


def _expose_services(registries: Registries, manifest: Manifest, spec: str, importer: Importer | None = None) -> int:
    """``backend.services`` entries are ``(ServiceRef, implementation)`` pairs exposed on the
    service locator under the manifest's id (released with the owner). A second exposer of
    the same ref by ANOTHER owner conflicts (the locator raises — "installed but does
    nothing" is exactly what the platform refuses); the same owner re-exposing (a second
    load into the same process) is a no-op."""
    count = 0
    for ref, impl in resolve_symbol(spec, importer):
        if registries.services.try_require(ref) is not None and registries.services.owner_of(ref) == manifest.id:
            continue
        registries.services.expose(ref, impl, owner=manifest.id)
        count += 1
    return count


def _register_hooks(registries: Registries, manifest: Manifest, spec: str, importer: Importer | None = None) -> int:
    """``backend.hooks`` entries are ``HookImplSpec``s produced by ``@hookimpl(name)``.

    The owner is the manifest id, never something the plugin chooses. The hook
    must already be declared on the registry (``UnknownEntry`` otherwise), so a
    typo in a hook name isolates the plugin instead of silently never firing.
    """
    count = 0
    for impl in _as_hook_impls(resolve_symbol(spec, importer), spec):
        registries.hooks.add(
            impl.hook, impl.fn, owner=manifest.id, tryfirst=impl.tryfirst, trylast=impl.trylast, wrapper=impl.wrapper
        )
        count += 1
    return count


@dataclass
class LoadPlan:
    ordered: list[Manifest]
    blocked: dict[str, str] = field(default_factory=dict)  # plugin id -> reason (deps_missing / blocked)


def plan_load(manifests: Iterable[Manifest]) -> LoadPlan:
    """Builtins in declaration order, then user plugins in dependency order (ties by id).

    ``dependencies`` are hard: a missing or version-mismatched dependency
    marks the dependant ``deps_missing``; a cycle marks every member
    ``blocked``; a dependant of a blocked plugin is blocked too (spec §9.6).
    ``afterDependencies`` only order (a missing one is ignored). Builtins are
    never reordered by user plugins. The result is deterministic, which is
    what keeps ``Registry.names()`` byte-stable across restarts.
    """
    from narranexus.kernel.plugins.compat import Range, Version

    items = list(manifests)
    by_id: dict[str, Manifest] = {}
    for m in items:
        if m.id in by_id:
            raise ManifestError(f"duplicate plugin id {m.id!r} in load set")
        by_id[m.id] = m
    builtins = [m for m in items if m.is_builtin]
    users = {m.id: m for m in items if not m.is_builtin}
    blocked: dict[str, str] = {}

    # 1. hard dependencies present + version match
    for pid, m in users.items():
        for dep, spec in m.dependencies.items():
            target = by_id.get(dep)
            if target is None:
                blocked[pid] = f"deps_missing: {dep} is not installed"
                break
            try:
                ok = Range.parse(spec).contains(Version.parse(target.version))
            except Exception as exc:  # noqa: BLE001 - malformed range/version
                blocked[pid] = f"blocked: dependency {dep} spec {spec!r} invalid: {exc}"
                break
            if not ok:
                blocked[pid] = f"deps_missing: {dep} {target.version} does not satisfy {spec}"
                break

    # 2. topological order over user plugins (hard deps + soft after-deps)
    edges: dict[str, set[str]] = {pid: set() for pid in users}
    for pid, m in users.items():
        for dep in list(m.dependencies) + list(m.after_dependencies):
            if dep in users:
                edges[pid].add(dep)
    order: list[str] = []
    state: dict[str, int] = {}  # 0 unseen, 1 visiting, 2 done
    cycle_members: set[str] = set()

    def visit(pid: str, stack: list[str]) -> None:
        if state.get(pid) == 2:
            return
        if state.get(pid) == 1:
            cycle_members.update(stack[stack.index(pid):])
            return
        state[pid] = 1
        stack.append(pid)
        for dep in sorted(edges[pid]):
            visit(dep, stack)
        stack.pop()
        state[pid] = 2
        order.append(pid)

    for pid in sorted(users):
        visit(pid, [])
    for pid in cycle_members:
        blocked[pid] = "blocked: dependency cycle " + " -> ".join(sorted(cycle_members))

    # 3. transitive blocking: depending on a blocked plugin blocks you
    changed = True
    while changed:
        changed = False
        for pid, m in users.items():
            if pid in blocked:
                continue
            for dep in m.dependencies:
                if dep in blocked:
                    blocked[pid] = f"blocked: dependency {dep} is {blocked[dep].split(':')[0]}"
                    changed = True
                    break

    ordered = builtins + [users[pid] for pid in order if pid not in blocked]
    return LoadPlan(ordered=ordered, blocked=blocked)


def load_order(manifests: Iterable[Manifest]) -> list[Manifest]:
    """The manifests that will load, in load order (blocked ones excluded)."""
    return plan_load(manifests).ordered


def _bounded_importer(manifest: Manifest, timeout_s: float) -> Importer:
    """Import a user plugin's own modules through the kernel importer's deadline door;
    anything else (the SDK, the platform) stays a plain import."""
    from narranexus.kernel.plugins.importer import import_plugin_module, package_name

    own = package_name(manifest.id)

    def _import(module_path: str) -> Any:
        if module_path == own or module_path.startswith(own + "."):
            return import_plugin_module(manifest.id, module_path[len(own) + 1:], timeout=timeout_s)
        return importlib.import_module(module_path)

    return _import


def load(
    registries: Registries,
    manifests: Iterable[Manifest],
    *,
    role: Host,
    import_timeout_s: float | None = None,
    deadline: float | None = None,
) -> LoadReport:
    """Register every contribution of the manifests that target ``role``.

    ``import_timeout_s`` bounds every USER plugin's imports (its own
    ``nxplugins.<id>`` modules go through ``importer.import_plugin_module`` on
    a daemon thread; a timeout records the plugin ``slow``, never crashed);
    ``deadline`` (a ``time.perf_counter()`` value) is re-checked before each
    user plugin so N wedged plugins cannot drag a host past its health-check
    window. Builtins are host code: plain imports, fail-fast.
    """
    from narranexus.kernel.plugins.importer import PluginImportTimeout

    report = LoadReport(role=role)
    seen: set[str] = set()
    plan = plan_load(manifests)
    for pid, reason in sorted(plan.blocked.items()):
        logger.warning(f"[plugins] {pid} not loaded: {reason}")
        report.loaded.append(PluginLoad(plugin_id=pid, version="", slots=(), entries=0, duration_ms=0.0, error=reason))
    # Pass 1 — every slot any loading plugin declares exists before ANY plugin
    # provides: ``builtin.frameworks.*`` fill ``turn.pipeline.act.framework``
    # (declared by ``builtin.turn``) whatever their relative load order; the
    # tree declares shallowest-first so a deeper declaration never pre-empts a
    # real one as an auto-namespace. Idempotent per process.
    declared: list[Slot] = []
    for manifest in plan.ordered:
        if manifest.id in seen:
            raise ManifestError(f"duplicate plugin id {manifest.id!r} in load set")
        seen.add(manifest.id)
        if role in manifest.effective_hosts():
            declared.extend(manifest.declared_slots())
    registries.slots.declare_all(declared)
    for manifest in plan.ordered:
        if role not in manifest.effective_hosts():
            report.skipped.append(manifest.id)
            continue
        if not manifest.is_builtin:
            missing_kinds = missing_api_kinds(manifest, registries.slots)
            if missing_kinds:
                report.warnings.append(
                    f"{manifest.id}: api does not version the contract kind(s) {list(missing_kinds)} of the slots it fills; "
                    "a future release refuses the manifest (docs/API_POLICY.md section 4)"
                )
        started = time.perf_counter()
        entries = 0
        error: str | None = None
        slow = False
        importer: Importer | None = None
        if not manifest.is_builtin:
            remaining = None if deadline is None else deadline - started
            if remaining is not None and remaining <= 0:
                report.loaded.append(PluginLoad(plugin_id=manifest.id, version=manifest.version, slots=(), entries=0, duration_ms=0.0,
                                                error="slow: stage deadline exceeded before this plugin loaded", slow=True))
                continue
            if import_timeout_s is not None:
                importer = _bounded_importer(manifest, min(import_timeout_s, remaining) if remaining is not None else import_timeout_s)
        try:
            for path, value in manifest.provides.items():
                specs = (value,) if isinstance(value, str) else value
                if path == HOOKS_SLOT:
                    registries.slots.get(path)
                    for spec in specs:
                        entries += _register_hooks(registries, manifest, spec, importer)
                    continue
                if path == SERVICES_SLOT:
                    registries.slots.get(path)
                    for spec in specs:
                        entries += _expose_services(registries, manifest, spec, importer)
                    continue
                registry = registries.registry_for(path)
                for spec in specs:
                    contributions = _as_contributions(resolve_symbol(spec, importer), spec)
                    if not contributions:
                        logger.debug(f"[plugins] {manifest.id}: {spec} produced no contributions")
                    for contribution in contributions:
                        registry.register_contribution(contribution, owner=manifest.id)
                        entries += 1
        except PluginImportTimeout as exc:
            # Slow is not broken: the plugin is left unloaded this boot, its
            # partial registrations withdrawn, and the host stays inside its
            # health-check window. The importer remembers the wedged module so
            # activation does not try the same import again.
            error, slow = f"slow: {exc}", True
            logger.warning(f"[plugins] {manifest.id}: too slow to import, skipped this boot: {exc}")
            registries.remove_owner(manifest.id)
        except Exception as exc:  # noqa: BLE001 — classify below
            error = f"{type(exc).__name__}: {exc}"
            if manifest.is_builtin:
                logger.error(f"[plugins] builtin {manifest.id} failed to load: {error}")
                raise
            logger.warning(f"[plugins] {manifest.id} failed to load and was isolated: {error}")
            # Isolation means NOTHING of it runs: the contributions and hooks
            # registered before the failing symbol are withdrawn, otherwise a
            # half-imported plugin's routes / triggers / tools kept being served
            # while the report said "isolated".
            try:
                withdrawn = registries.remove_owner(manifest.id)
                if withdrawn:
                    logger.info(f"[plugins] {manifest.id}: {withdrawn} partial registration(s) withdrawn")
            except Exception as rollback_exc:  # noqa: BLE001 — the rollback must not fail the boot
                logger.error(f"[plugins] {manifest.id}: could not withdraw partial registrations: {rollback_exc}")
        report.loaded.append(
            PluginLoad(
                plugin_id=manifest.id,
                version=manifest.version,
                slots=tuple(manifest.provides),
                entries=entries,
                duration_ms=(time.perf_counter() - started) * 1000.0,
                error=error,
                slow=slow,
            )
        )
    return report


__all__ = ["Discovery", "HOOKS_SLOT", "SERVICES_SLOT", "LoadPlan", "PluginLoad", "LoadReport", "discover", "load", "load_order", "plan_load", "resolve_symbol"]
