"""
@file_name: service.py
@author: Bin Liang
@date: 2026-09-03
@description: ``FactoryService`` — every mutation of user-plugin state goes through here, and from here only through ``lifecycle``/``Installer``.

The service composes the kernel pieces the factory page needs: the
registry store (state), the installer (sources/deps/placement), the index
(search + blocklist), the bisect helper, the last boot report (what
loaded / what was isolated) and a bounded per-plugin error log fed by the
frontend error sink. Cloud deployments get a read-only view: every mutation
raises ``CloudManaged`` so the route answers 403 before anything runs.
"""
from __future__ import annotations

import time
from collections import deque

from loguru import logger
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from narranexus.hosts.boot import BootReport
from narranexus.kernel.deployment import is_cloud_mode
from narranexus.kernel.plugins.bisect import Bisect
from narranexus.kernel.plugins.install import Installer, InstallResult
from narranexus.kernel.plugins.install.index import Index
from narranexus.kernel.plugins.install.installer import InstallError
from narranexus.kernel.plugins.lifecycle import BootMarker, RegistryError, RegistryStore
from narranexus.kernel.plugins.manifest import Manifest, derive_activation_events, load_manifest
from narranexus.kernel.plugins.paths import MANIFEST_FILENAME, frontend_dist_dir, plugin_home, registry_path
from narranexus.kernel.plugins.builtins import check_builtin_toggle, refuse_if_excluded, slot_tree_with_builtins

ERROR_LOG_PLUGINS_LIMIT = 64  # distinct plugin ids that keep an in-memory error log
ERROR_LOG_LIMIT = 50


class CloudManaged(Exception):
    """Mutations are refused in cloud mode (plugins are baked into the image)."""


class NotInstalled(KeyError):
    pass


@dataclass
class UiError:
    at: float
    kind: str
    message: str
    stack: str = ""


# Every host role that keeps a boot-crash marker next to registry.json.
_BOOT_ROLES: tuple[str, ...] = ("backend", "mcp", "workers")


@dataclass
class FactoryService:
    store: RegistryStore = field(default_factory=lambda: RegistryStore(path=registry_path()))
    installer: Installer | None = None
    index: Index | None = None
    boot_report: BootReport | None = None
    _errors: dict[str, deque[UiError]] = field(default_factory=dict)

    # ------------------------------------------------------------ helpers

    def _installer(self) -> Installer:
        if self.installer is None:
            # The blocklist ALWAYS comes from the index (the previous code only
            # consulted it when someone had happened to open the index first —
            # a security control that depended on click order). Decision when
            # the index is unreachable: fail OPEN with a loud log, because an
            # offline desktop must still install a local plugin; the block is a
            # revocation signal, not the only gate (permissions, isolation).
            try:
                blocked: Any = self._index().blocked()
            except Exception as exc:  # noqa: BLE001 — offline / index down, no cache
                # Fail CLOSED for remote sources (the installer refuses them
                # while the blocklist is unknown), open for local paths: an
                # offline desktop must still develop a plugin.
                logger.warning(f"[plugins] index blocklist unavailable: {exc} — remote installs refused until it is")
                blocked = None
            self.installer = Installer(store=self.store, blocked=blocked)
        return self.installer

    def _index(self) -> Index:
        if self.index is None:
            self.index = Index(cache_dir=plugin_home() / ".index-cache")
        return self.index

    @staticmethod
    def _guard_mutation() -> None:
        if is_cloud_mode():
            raise CloudManaged("plugins are managed by the platform in cloud mode")

    def _excluded_builtins(self) -> frozenset[str]:
        """Builtins the RUNNING distribution leaves out (never loaded, routes never mounted).

        The lifespan boot's report is authoritative (``backend/main.py`` hands it over). Until it
        exists the backend's own resolved distribution answers, so a listing is never more
        optimistic than the process.
        """
        if self.boot_report is not None:
            return frozenset(self.boot_report.excluded_builtins)
        from backend import plugins_boot

        res = plugins_boot.distribution()
        return frozenset(res.excluded) if res is not None else frozenset()

    def _refuse_if_excluded(self, plugin_id: str) -> None:
        refuse_if_excluded(plugin_id, self._excluded_builtins())

    def _manifest_of(self, path: Path) -> Manifest | None:
        try:
            return load_manifest(path / MANIFEST_FILENAME, tree=slot_tree_with_builtins())
        except Exception:  # noqa: BLE001 — a broken manifest is reported through state, not here
            return None

    # ------------------------------------------------------------ listing

    def list(self) -> dict[str, Any]:
        reg = self.store.read()
        boot = self.boot_report
        rows: list[dict[str, Any]] = []
        for pid, rec in sorted(reg.plugins.items()):
            manifest = self._manifest_of(Path(rec.path))
            rows.append(
                {
                    "id": pid,
                    "display_name": manifest.display_name if manifest else pid,
                    "description": manifest.description if manifest else "",
                    "version": rec.installed_version,
                    "mode": rec.mode,
                    "path": rec.path,
                    "source": rec.source.model_dump(),
                    "enabled": rec.enabled,
                    "state": rec.state,
                    "scope": rec.scope,
                    "last_error": rec.last_error,
                    "crash_count": rec.crash_count,
                    "warnings": list(rec.warnings),
                    "permissions": manifest.permissions.model_dump() if manifest else {},
                    "permissions_acknowledged": rec.permissions_acknowledged,
                    "installed_by": rec.installed_by,
                    "installed_at": rec.installed_at,
                    "provides": sorted(manifest.provides) if manifest else [],
                    "frontend": manifest.frontend.model_dump(mode="json") if manifest and manifest.frontend else None,
                    "activation_events": list(derive_activation_events(manifest)) if manifest else [],
                    "protected": bool(manifest.protected) if manifest else False,
                    "size": manifest.size.model_dump() if manifest else {},
                    "loaded": bool(boot and pid in boot.user_plugin_ids),
                    "isolated": (boot.isolated.get(pid) if boot else None),
                    "recent_errors": len(self._errors.get(pid, ())),
                }
            )
        return {
            "plugins": rows,
            "builtins": self._builtin_rows(reg),
            "safe_mode": reg.safe_mode,
            "safe_mode_reason": reg.safe_mode_reason,
            "bisect": reg.bisect.model_dump() if reg.bisect else None,
            "cloud_managed": is_cloud_mode(),
            "boot": (
                {
                    "role": boot.role,
                    "loaded": list(boot.user_plugin_ids),
                    "isolated": dict(boot.isolated),
                    "rejected": dict(boot.rejected),
                    "duration_ms": boot.duration_ms,
                }
                if boot
                else None
            ),
        }

    def _builtin_rows(self, reg) -> list[dict[str, Any]]:
        from narranexus.kernel.plugins.builtins import builtin_manifests

        from narranexus.kernel.plugins.install.builtin_deps import is_on_demand

        deps_missing = dict(self.boot_report.deps_missing) if self.boot_report is not None else {}
        excluded = self._excluded_builtins()
        rows = []
        for m in builtin_manifests():
            override = reg.builtin_overrides.get(m.id, {})
            rows.append(
                {
                    "id": m.id,
                    "display_name": m.display_name,
                    "description": m.description,
                    "version": m.version,
                    # An excluded builtin is not running at all, whatever registry.json says. The frontend
                    # drops the UI of an excluded row, and of a not-enabled row unless it is protected.
                    "enabled": m.id not in excluded and override.get("enabled", True) is not False,
                    "excluded": m.id in excluded,
                    "protected": bool(m.protected),
                    "hosts": list(m.hosts),
                    "provides": sorted(m.provides),
                    "dependencies": dict(m.dependencies),
                    "on_demand": is_on_demand(m),
                    "pip": list(m.backend.pip) if m.backend is not None else [],
                    "deps_missing": deps_missing.get(m.id),
                }
            )
        return rows

    def install_builtin_deps(self, plugin_id: str) -> dict[str, Any]:
        """Retry an on-demand builtin's dependency install (local build only); a restart picks it up."""
        self._guard_mutation()
        from narranexus.kernel.plugins.builtins import builtin_manifests
        from narranexus.kernel.plugins.install.builtin_deps import ensure_builtin_deps, is_on_demand

        manifest = next((m for m in builtin_manifests() if m.id == plugin_id), None)
        if manifest is None:
            raise NotInstalled(f"{plugin_id} is not a builtin plugin")
        self._refuse_if_excluded(plugin_id)
        if not is_on_demand(manifest):
            raise RegistryError(f"{plugin_id} has no on-demand dependencies")
        status = ensure_builtin_deps(manifest, cloud=False, runner=self._installer().runner)
        if not status.ok:
            raise InstallError(status.error or "dependency install failed")
        if self.boot_report is not None:
            self.boot_report.deps_missing.pop(plugin_id, None)
        return {"id": plugin_id, "installed": list(status.installed), "restart_required": True}

    def set_builtin_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        """Toggle a builtin through registry.json builtin_overrides (protected builtins refuse)."""
        self._guard_mutation()
        from narranexus.kernel.plugins.builtins import builtin_manifests

        excluded = self._excluded_builtins()
        try:
            check_builtin_toggle(plugin_id, enabled, excluded=excluded)
        except KeyError as exc:
            raise NotInstalled(f"{plugin_id} is not a builtin plugin") from exc
        # A dependant the distribution never loads gets no override and is not reported as also-disabled.
        dependants = [m.id for m in builtin_manifests() if plugin_id in m.dependencies and m.id not in excluded]

        def _mutate(reg):
            if enabled:
                reg.builtin_overrides.pop(plugin_id, None)
            else:
                reg.builtin_overrides[plugin_id] = {"enabled": False}
                for dep in dependants:  # a disabled dependency disables its dependants too (spec §9.6)
                    reg.builtin_overrides[dep] = {"enabled": False, "because": plugin_id}

        reg = self.store.update(_mutate)
        return {"id": plugin_id, "enabled": enabled, "also_disabled": dependants if not enabled else [], "restart_required": True}

    # ---------------------------------------------------------- mutations

    def install(self, source: str, *, permissions_acknowledged: bool = False, scope: str = "global") -> InstallResult:
        self._guard_mutation()
        return self._installer().install(source, permissions_acknowledged=permissions_acknowledged, scope=scope)

    def uninstall(self, plugin_id: str, *, purge_files: bool = True) -> None:
        self._guard_mutation()
        try:
            self._installer().uninstall(plugin_id, purge_files=purge_files)
        except Exception as exc:  # noqa: BLE001
            raise NotInstalled(str(exc)) from exc

    def set_enabled(self, plugin_id: str, enabled: bool) -> dict[str, Any]:
        self._guard_mutation()
        from narranexus.kernel.plugins.install.installer import is_gated

        try:
            current = self.store.read().plugins.get(plugin_id)
            if current is None:
                raise RegistryError(f"unknown plugin {plugin_id!r}")
            if enabled and is_gated(current):
                # the disclosure is a gate: enabling without acknowledging is refused
                raise InstallError(f"{plugin_id} declares permissions that are not acknowledged; acknowledge them first")
            rec = self.store.set_enabled(plugin_id, enabled)
        except RegistryError as exc:
            raise NotInstalled(str(exc)) from exc
        return rec.model_dump()

    def acknowledge_permissions(self, plugin_id: str) -> dict[str, Any]:
        self._guard_mutation()
        from narranexus.kernel.plugins.install.installer import InstallError as _IE
        from narranexus.kernel.plugins.install.installer import acknowledge_permissions

        try:
            return acknowledge_permissions(self.store, plugin_id).model_dump()
        except (RegistryError, _IE) as exc:
            raise NotInstalled(str(exc)) from exc

    def rollback(self) -> dict[str, Any]:
        self._guard_mutation()
        reg = self.store.rollback_to_lkg()
        return {"plugins": sorted(reg.plugins), "safe_mode": reg.safe_mode}

    def leave_safe_mode(self) -> dict[str, Any]:
        self._guard_mutation()
        reg = self.store.set_safe_mode(False)
        # The user is saying "try again": the consecutive-failure counters
        # start from zero as well. Otherwise a counter left at two re-enters
        # safe mode on the very next boot before any plugin had a chance.
        for role in _BOOT_ROLES:
            BootMarker(role, path=self.store.path.parent / f".booting-{role}").exit()
        return {"safe_mode": reg.safe_mode}

    def upgrade(self, plugin_id: str) -> InstallResult:
        self._guard_mutation()
        try:
            return self._installer().upgrade(plugin_id)
        except Exception as exc:  # noqa: BLE001
            raise NotInstalled(str(exc)) from exc

    def check_update(self, plugin_id: str) -> dict[str, Any]:
        try:
            return asdict(self._installer().check_update(plugin_id))
        except Exception as exc:  # noqa: BLE001
            raise NotInstalled(str(exc)) from exc

    # -------------------------------------------------------------- bisect

    def bisect_start(self) -> dict[str, Any]:
        self._guard_mutation()
        return asdict(Bisect(self.store).start())

    def bisect_answer(self, good: bool) -> dict[str, Any]:
        self._guard_mutation()
        return asdict(Bisect(self.store).answer(good=good))

    def bisect_stop(self) -> dict[str, Any]:
        self._guard_mutation()
        Bisect(self.store).stop()
        return {"stopped": True}

    # --------------------------------------------------------------- index

    def search_index(self, query: str = "") -> list[dict[str, Any]]:
        return [asdict(e) for e in self._index().search(query)]

    # -------------------------------------------------------------- errors

    def slots(self) -> dict[str, Any]:
        """The slot catalog of this process's own registries (what `narranexus slots` shows)."""
        from narranexus.kernel.plugins.catalog import slot_catalog
        from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES

        return {"domains": slot_catalog(KERNEL_REGISTRIES)}

    def record_error(self, plugin_id: str, *, kind: str, message: str, stack: str = "") -> int:
        # A mutation like every other sibling: the guard is the method's FIRST
        # statement, not the route's, so `_run`'s `except CloudManaged` turns it
        # into the same 403 instead of letting it escape as a 500 (the frontend
        # error sink cannot tell "refused by design" from "backend broken").
        # The GETTER stays unguarded — reading the log is a read.
        self._guard_mutation()
        # Only installed plugins have an error log: the id is a raw path
        # segment, and an unbounded dict keyed by it was a memory leak.
        from narranexus.kernel.plugins.builtins import builtin_manifests

        if plugin_id not in self.store.read().plugins and not any(m.id == plugin_id for m in builtin_manifests()):
            raise NotInstalled(f"{plugin_id} is not installed")
        if plugin_id not in self._errors and len(self._errors) >= ERROR_LOG_PLUGINS_LIMIT:
            oldest = next(iter(self._errors))
            del self._errors[oldest]
        log = self._errors.setdefault(plugin_id, deque(maxlen=ERROR_LOG_LIMIT))
        log.append(UiError(at=time.time(), kind=kind, message=message[:2000], stack=stack[:8000]))
        try:
            # The self-extension observation window reads these rows (audit timeline in the plugin home).
            from narranexus_plugins.nexus_plugins_module._nexus_plugins_impl.state import Audit

            Audit().record(agent_id="", user_id="", plugin_id=plugin_id, action="ui_error", why=kind, extra={"message": message[:500]})
        except Exception as exc:  # noqa: BLE001 — never fail an error report
            logger.debug(f"[plugins] audit write skipped: {exc}")
        return len(log)

    # ----------------------------------------------------------- proposals

    def proposals(self, *, user_id: str, pending_only: bool = True) -> list[dict[str, Any]]:
        """The caller's OWN agents' proposals — never another user's (they carry summaries and requested permissions)."""
        from narranexus_plugins.nexus_plugins_module._nexus_plugins_impl.state import ProposalStore

        return [asdict(p) for p in ProposalStore().list(pending_only=pending_only) if p.user_id == user_id]

    def decide_proposal(self, proposal_id: str, *, approved: bool, by: str) -> dict[str, Any]:
        self._guard_mutation()
        from narranexus_plugins.nexus_plugins_module._nexus_plugins_impl.service import SelfExtensionService
        from narranexus_plugins.nexus_plugins_module._nexus_plugins_impl.state import ProposalStore

        p = ProposalStore().get(proposal_id)
        # Not-found for someone else's proposal too: an existence oracle across
        # users is a leak. The APPLY keeps using the proposal's own identity.
        if p is None or p.user_id != by:
            raise NotInstalled(f"proposal {proposal_id} not found")
        svc = SelfExtensionService(p.agent_id, p.user_id, store=self.store)
        try:
            return svc.apply_decision(proposal_id, "approved" if approved else "rejected", by=by)
        except ValueError as exc:
            raise RegistryError(str(exc)) from exc

    def errors(self, plugin_id: str) -> list[dict[str, Any]]:
        return [asdict(e) for e in self._errors.get(plugin_id, ())]

    # -------------------------------------------------------------- assets

    def asset_path(self, plugin_id: str, relative: str) -> Path:
        """Resolve a frontend asset under the plugin's ``frontend/dist`` with a traversal guard."""
        rec = self.store.read().plugins.get(plugin_id)
        if rec is None:
            raise NotInstalled(plugin_id)
        root = frontend_dist_dir(Path(rec.path)).resolve()
        candidate = (root / relative).resolve()
        if root not in candidate.parents and candidate != root:
            raise PermissionError(f"{relative!r} escapes the plugin's asset directory")
        if not candidate.is_file():
            raise FileNotFoundError(relative)
        return candidate


__all__ = ["CloudManaged", "ERROR_LOG_LIMIT", "FactoryService", "NotInstalled", "UiError"]
