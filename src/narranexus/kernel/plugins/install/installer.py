"""
@file_name: installer.py
@author: Bin Liang
@date: 2026-09-03
@description: The one install pipeline: fetch → validate → deps → place → register; plus uninstall, upgrade and update checks.
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import httpx
from loguru import logger

from narranexus.contracts import ManifestError, PluginError
from narranexus.kernel.plugins.compat import blocked_reason, host_version, select_version
from narranexus.kernel.plugins.install.deps import Runner, install_deps
from narranexus.kernel.plugins.install.sources import GitHubReleaseSource, GitHubRepoSource, LocalSource, Source, parse_source_spec
from narranexus.kernel.plugins.lifecycle import PluginRecord, RegistryStore
from narranexus.kernel.plugins.manifest import Manifest, load_manifest
from narranexus.kernel.plugins.paths import MANIFEST_FILENAME, VERSIONS_FILENAME, deps_dir, plugin_dir, plugin_home, registry_path
from narranexus.kernel.plugins.builtins import slot_tree_with_builtins


class InstallError(PluginError):
    pass


@dataclass
class InstallResult:
    plugin_id: str
    version: str
    path: Path
    mode: str
    warnings: list[str] = field(default_factory=list)
    deps_installed: tuple[str, ...] = ()
    permissions: dict[str, Any] = field(default_factory=dict)
    restart_required: bool = True


@dataclass
class UpdateCheck:
    plugin_id: str
    installed: str
    available: str | None
    source: str


@dataclass
class Installer:
    store: RegistryStore = field(default_factory=lambda: RegistryStore(path=registry_path()))
    client: httpx.Client | None = None
    runner: Runner | None = None
    # None = the blocklist could not be obtained: remote installs are refused
    # (fail-closed for code that comes from the network), local paths still
    # install (an offline desktop must be able to develop a plugin).
    blocked: Mapping[str, Mapping[str, str]] | None = field(default_factory=dict)
    host: str = field(default_factory=host_version)

    # ------------------------------------------------------------- install

    def install(
        self,
        source: Source | str,
        *,
        installed_by: str = "user",
        scope: str = "global",
        permissions_acknowledged: bool = False,
        replace: bool = False,
    ) -> InstallResult:
        src = parse_source_spec(source) if isinstance(source, str) else source
        if self.blocked is None and not isinstance(src, LocalSource):
            raise InstallError(
                f"cannot install {src.describe()}: the plugin index blocklist is unavailable (offline, no cached copy) — "
                "retry online, or install from a local path"
            )
        staging = Path(tempfile.mkdtemp(prefix="nx-plugin-install-", dir=str(plugin_home())))
        try:
            fetched = src.fetch(staging, self.client)
            manifest = self._validate(fetched.root)
            reason = blocked_reason(self.blocked or {}, manifest.id, manifest.version)
            if reason:
                raise InstallError(f"{manifest.id} {manifest.version} is blocked: {reason}")
            installed = self.store.read().plugins
            existing = installed.get(manifest.id)
            if existing is not None and not replace:
                raise InstallError(f"{manifest.id} is already installed ({existing.installed_version}); use upgrade")
            # One slug, one plugin: the table prefix, the settings env prefix and
            # the synthetic package are all derived from the slug (not injective
            # over ids — ``acme.auth-sso`` and ``acme.auth_sso`` collide).
            from narranexus.contracts import plugin_id_slug
            from narranexus.kernel.plugins.builtins import builtin_manifests

            slug = plugin_id_slug(manifest.id)
            holders = [pid for pid in [*installed, *(m.id for m in builtin_manifests())] if pid != manifest.id and plugin_id_slug(pid) == slug]
            if holders:
                raise InstallError(
                    f"cannot install {manifest.id}: its identifier form {slug!r} is already held by {holders[0]!r} "
                    f"(table prefix, settings and package names would collide); pick another id"
                )
            if existing is not None and existing.mode == "copy" and fetched.mode != "link":
                # replacing: the old copy goes away only after the new one is in place
                pass
            deps = self._install_deps(manifest, fetched.root, fetched.mode)
            final_path = self._place(manifest, fetched.root, fetched.mode, existing)
            # Disclosure is a GATE: a plugin that declares any permission stays
            # disabled ("registered") until the user acknowledges — enabling is
            # what `narranexus plugin enable --ack` / the factory's acknowledge
            # endpoint do. Without this the printed "acknowledge with …" hint
            # was decoration and the plugin ran on the next boot regardless.
            # Re-acknowledgement is required when an upgrade widens permissions.
            needs_ack = declares_permissions(manifest) and not permissions_acknowledged
            record = PluginRecord(
                path=str(final_path),
                mode="link" if fetched.mode == "link" else "copy",
                source=fetched.record,
                installed_version=manifest.version,
                scope=scope,
                warnings=list(fetched.warnings) + ([PERMISSIONS_PENDING_WARNING] if needs_ack else []),
                permissions_acknowledged=permissions_acknowledged,
                acknowledged_permissions=sorted(permission_tokens(manifest)) if permissions_acknowledged else [],
                installed_by=installed_by,
                enabled=not needs_ack,
            )
            if existing is not None:
                self.store.unregister(manifest.id)
            self.store.register(manifest.id, record)
            logger.info(f"[plugins] installed {manifest.id} {manifest.version} from {src.describe()} → {final_path}")
            return InstallResult(
                plugin_id=manifest.id,
                version=manifest.version,
                path=final_path,
                mode=record.mode,
                warnings=list(fetched.warnings),
                deps_installed=deps,
                permissions=manifest.permissions.model_dump(mode="json"),
            )
        finally:
            if staging.exists():
                shutil.rmtree(staging, ignore_errors=True)

    def _validate(self, root: Path) -> Manifest:
        mpath = root / MANIFEST_FILENAME
        try:
            return load_manifest(mpath, tree=slot_tree_with_builtins(), host_version=self.host)
        except ManifestError as exc:
            versions = root / VERSIONS_FILENAME
            if versions.is_file():
                try:
                    fitting = select_version(json.loads(versions.read_text(encoding="utf-8")), self.host)
                except (ValueError, TypeError):
                    fitting = None
                if fitting:
                    raise InstallError(f"{exc}. versions.json suggests {fitting} for this host; install that tag instead") from None
            raise InstallError(str(exc)) from None

    def _install_deps(self, manifest: Manifest, root: Path, mode: str) -> tuple[str, ...]:
        if manifest.backend is None or not manifest.backend.pip or manifest.install.deps == "on_demand":
            return ()
        target = deps_dir(manifest.id, mode="link" if mode == "link" else "copy", plugin_path=root)
        result = install_deps(manifest.backend.pip, target, runner=self.runner)
        return result.requirements if result else ()

    def _place(self, manifest: Manifest, root: Path, mode: str, existing: PluginRecord | None) -> Path:
        if mode == "link":
            return root
        dest = plugin_dir(manifest.id)
        if dest.exists():
            backup = dest.with_name(dest.name + f".old-{int(time.time())}")
            shutil.move(str(dest), str(backup))
            shutil.move(str(root), str(dest))
            shutil.rmtree(backup, ignore_errors=True)
        else:
            shutil.move(str(root), str(dest))
        # deps were installed into <staging>/pyenv; they moved with the directory
        return dest

    # ----------------------------------------------------------- uninstall

    def uninstall(self, plugin_id: str, *, purge_files: bool = True) -> None:
        reg = self.store.read()
        rec = reg.plugins.get(plugin_id)
        if rec is None:
            raise InstallError(f"{plugin_id} is not installed")
        self.store.unregister(plugin_id)
        if purge_files and rec.mode == "copy":
            path = Path(rec.path)
            if path.exists() and plugin_home().resolve() in path.resolve().parents:
                shutil.rmtree(path, ignore_errors=True)
        if rec.mode == "link":
            deps = deps_dir(plugin_id, mode="link")
            if deps.exists():
                shutil.rmtree(deps, ignore_errors=True)
        logger.info(f"[plugins] uninstalled {plugin_id} (files {'removed' if purge_files else 'kept'})")

    # ------------------------------------------------------------- upgrade

    def check_update(self, plugin_id: str) -> UpdateCheck:
        rec = self.store.read().plugins.get(plugin_id)
        if rec is None:
            raise InstallError(f"{plugin_id} is not installed")
        available: str | None = None
        if rec.source.type == "github" and rec.source.repo:
            from narranexus.kernel.plugins.install.sources import _http

            with _http(self.client) as client:
                resp = client.get(f"https://api.github.com/repos/{rec.source.repo}/releases/latest", timeout=20.0, follow_redirects=True)
            if resp.status_code == 200:
                tag = str(resp.json().get("tag_name") or "").lstrip("v")
                available = tag if tag and tag != rec.installed_version.lstrip("v") else None
        return UpdateCheck(plugin_id, rec.installed_version, available, rec.source.type)

    def upgrade(self, plugin_id: str, *, to_tag: str | None = None) -> InstallResult:
        rec = self.store.read().plugins.get(plugin_id)
        if rec is None:
            raise InstallError(f"{plugin_id} is not installed")
        acknowledged = frozenset(rec.acknowledged_permissions)
        if rec.source.type == "github":
            source: Source = GitHubReleaseSource(rec.source.repo, to_tag or "")
        elif rec.source.type == "github_repo":
            source = GitHubRepoSource(rec.source.repo, to_tag or rec.source.ref)
        else:
            source = LocalSource(Path(rec.path), mode=rec.mode)
        result = self.install(
            source, installed_by=rec.installed_by, scope=rec.scope, permissions_acknowledged=rec.permissions_acknowledged, replace=True
        )
        # An upgrade that asks for MORE than the user acknowledged re-arms the
        # gate: the new record is disabled until acknowledged again.
        if rec.permissions_acknowledged:
            new_rec = self.store.read().plugins.get(plugin_id)
            if new_rec is not None and not _permissions_of_installed(new_rec) <= acknowledged:
                self.store.update(lambda reg: _require_reack(reg, plugin_id))
        return result



PERMISSIONS_PENDING_WARNING = "permissions not acknowledged: disabled until `narranexus plugin enable <id> --ack` (or the factory's acknowledge)"


def declares_permissions(manifest: Any) -> bool:
    p = manifest.permissions
    return bool(p.network or p.filesystem or p.subprocess or p.env)


def permission_tokens(manifest: Any) -> frozenset[str]:
    """A manifest's declared permissions as comparable tokens."""
    p = manifest.permissions
    tokens = {f"network:{n}" for n in p.network} | {f"filesystem:{f}" for f in p.filesystem} | {f"env:{e}" for e in p.env}
    if p.subprocess:
        tokens.add("subprocess")
    return frozenset(tokens)


def _permissions_of_installed(rec: Any) -> frozenset[str]:
    """The permission tokens of an installed plugin's manifest on disk (empty when unreadable).

    Reads the ``permissions`` block of the JSON directly: the gate must work
    even when the full manifest no longer validates against this host (an
    incompatible plugin is still a plugin whose permissions were or were not
    acknowledged).
    """
    from narranexus.kernel.plugins.manifest import Permissions
    from narranexus.kernel.plugins.paths import manifest_path

    try:
        raw = json.loads(manifest_path(Path(rec.path)).read_text(encoding="utf-8"))
        return permission_tokens(type("_M", (), {"permissions": Permissions.model_validate(raw.get("permissions") or {})})())
    except Exception:  # noqa: BLE001 — a missing/broken manifest cannot widen anything
        return frozenset()


def acknowledge_permissions(store: Any, plugin_id: str) -> Any:
    """Record the user's acknowledgement of a plugin's CURRENT declared permissions and enable it.

    The one door for the CLI's ``plugin enable --ack`` and the factory's
    acknowledge endpoint: both must store the tokens the acknowledgement
    covered, or an upgrade cannot tell whether it widened them.
    """
    rec = store.read().plugins.get(plugin_id)
    if rec is None:
        raise InstallError(f"{plugin_id} is not installed")
    tokens = sorted(_permissions_of_installed(rec))

    def _mutate(reg: Any) -> None:
        r = reg.plugins[plugin_id]
        r.permissions_acknowledged = True
        r.acknowledged_permissions = tokens
        r.enabled = True
        if r.state == "disabled":
            r.state = "registered"
        r.warnings = [w for w in r.warnings if PERMISSIONS_PENDING_WARNING not in w]

    return store.update(_mutate).plugins[plugin_id]


def is_gated(rec: Any) -> bool:
    """True when the plugin declares permissions the user has not acknowledged (it must not be enabled)."""
    return bool(_permissions_of_installed(rec)) and not rec.permissions_acknowledged


def _require_reack(reg: Any, plugin_id: str) -> None:
    rec = reg.plugins[plugin_id]
    rec.permissions_acknowledged = False
    rec.acknowledged_permissions = []
    rec.enabled = False
    rec.state = "registered"
    rec.warnings.append("upgrade widened the declared permissions: " + PERMISSIONS_PENDING_WARNING)


__all__ = ["InstallError", "InstallResult", "Installer", "PERMISSIONS_PENDING_WARNING", "UpdateCheck", "acknowledge_permissions", "declares_permissions", "is_gated", "permission_tokens"]
