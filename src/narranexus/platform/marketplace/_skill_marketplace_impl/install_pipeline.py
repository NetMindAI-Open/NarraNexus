"""
@file_name: install_pipeline.py
@author: NetMind.AI
@date: 2026-07-20
@description: Unified 7-step install engine for skills (spec §5).

Every install/uninstall entrance (UI zip/GitHub, URL, agent MCP tools,
future marketplace source) converges here:

1. Resolve      — manifest dependencies must already be installed
2. Validate     — compatibility.narranexus_min/max vs running version
3. Conflict     — same version installed -> skip; different -> replace,
                  migrating the old env_config (same-key auto-migration)
4. Download     — staging (zip extract / git clone) + security scan gate:
                  HIGH -> reject before skills/ is touched, LOW -> warnings
5. Unpack       — SkillModule.install_from_dir (existing safety checks)
6. Inject cfg   — restore migrated env_config, report config_required
7. Lock & audit — .skill_meta.json gains hash/content_hash/updated_at;
                  skill_installations upsert; auto-archive backup

Disk is the single source of truth: a DB audit failure is logged, never
rolled back onto the filesystem.
"""

import hashlib
import json
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from narranexus.contracts.skill import SkillWorkspace
from narranexus.platform.utils.plugin_services import skill_workspace
from narranexus.platform.utils.workspace_paths import safe_id_segment
from narranexus.platform.schema.skill_schema import SkillInfo

from .scanner import scan_skill_dir


@dataclass
class InstallResult:
    status: str  # installed | already_installed | failed
    skill: Optional[SkillInfo]
    warnings: List[Dict[str, Any]] = field(default_factory=list)
    config_required: bool = False
    replaced_version: Optional[str] = None
    # ``failed`` only (multi-skill GitHub installs): the SKILL.md name of the
    # root that was rejected and ``_failure_text(exc)`` — "<Class>: <msg>"
    # capped at INSTALL_ERROR_MAX_CHARS (see the constant's note below); any
    # exception class, not only gate ValueErrors. ``skill`` is None then.
    skill_name: Optional[str] = None
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.status != "failed"


# Per-skill failure text handed to the route / MCP tool (agent-visible):
# exception class + message so a bare KeyError / empty RuntimeError still
# says what happened, capped so a driver's multi-KB message cannot bloat
# the response. No URL/token masking here — the only URL on this path is
# the repository address the user supplied.
INSTALL_ERROR_MAX_CHARS = 500


def _failure_text(exc: BaseException) -> str:
    return f"{type(exc).__name__}: {exc}".replace("\n", " ")[:INSTALL_ERROR_MAX_CHARS]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def compute_content_hash(skill_dir: Path) -> str:
    """Deterministic content hash of a skill directory.

    Excludes .skill_meta.json (install bookkeeping mutates it), so the hash
    is stable across meta updates and comparable by the reconciler.
    """
    digest = hashlib.sha256()
    for path in sorted(skill_dir.rglob("*")):
        if not path.is_file() or path.name == ".skill_meta.json":
            continue
        digest.update(path.relative_to(skill_dir).as_posix().encode("utf-8"))
        digest.update(path.read_bytes())
    return f"sha256:{digest.hexdigest()}"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _version_tuple(version: str) -> tuple:
    numbers = re.findall(r"\d+", version.split("-")[0].split("+")[0])
    return tuple(int(n) for n in numbers[:3]) + (0,) * (3 - len(numbers[:3]))


def _current_app_version() -> Optional[str]:
    try:
        from narranexus.kernel.plugins.compat import host_version

        return host_version()
    except Exception:
        return None


class InstallPipeline:
    """Skill install/uninstall engine for one (agent_id, user_id) workspace."""

    def __init__(
        self,
        agent_id: str,
        user_id: str,
        skill_module: Optional[SkillWorkspace] = None,
        db_client=None,
    ):
        self.agent_id = agent_id
        self.user_id = user_id
        self.skill_module = skill_module or skill_workspace(agent_id, user_id)
        self._db_client = db_client

    # -- public entrances ----------------------------------------------------

    async def install_from_zip(self, zip_path: Path) -> InstallResult:
        temp_dir = Path(tempfile.mkdtemp())
        try:
            skill_root = self.skill_module.extract_skill_package(zip_path, temp_dir)
            return await self._install_staged(
                skill_root,
                source_type="zip",
                source_url=None,
                package_hash=_sha256_file(zip_path),
                original_zip_path=zip_path,
            )
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    async def install_from_github(self, url: str, branch: str = "main") -> List[InstallResult]:
        """Install every skill the repository ships — one result per skill.

        A single-skill repo (root or nested SKILL.md) yields one result; a
        multi-skill repo (``skills/<name>/SKILL.md`` or ``<name>/SKILL.md``)
        yields one per skill. Each skill runs the full staged pipeline
        (scan gate, dependency/compat checks, conflict/config migration,
        audit) on its own and a rejection is isolated: it becomes a
        ``status="failed"`` result carrying ``skill_name`` + ``error`` while
        the others still install — so "security scan rejected X" never
        masks "Y and Z were installed". Roots are installed dependencies
        first (manifest ``dependencies`` that name a sibling in the same
        repo), name order otherwise, so a same-repo dependency chain works
        whatever the directory names sort to. Never empty: a repo without
        any SKILL.md raises ValueError (clone / layout problems are not
        per-skill failures).
        """
        temp_dir = Path(tempfile.mkdtemp())
        try:
            skill_roots, canonical_url = self.skill_module.fetch_github_repo(url, branch, temp_dir)
            results: List[InstallResult] = []
            for skill_root, skill_name in self._order_roots_by_dependency(skill_roots):
                try:
                    results.append(
                        await self._install_staged(
                            skill_root,
                            source_type="github",
                            source_url=canonical_url,
                            package_hash=None,
                            branch=branch,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 — one root must never sink its siblings
                    # ValueError = a gate said no (scan / deps / compat /
                    # manifest); OSError = disk full / permissions; anything
                    # else = a bug inside this root's install. All per-skill:
                    # the siblings already installed stay reported (disk is
                    # truth, nothing rolls back), this one is failed. Repo-
                    # level errors (fetch, layout, cap) are outside this loop
                    # and still raise.
                    error = _failure_text(exc)
                    logger.warning(f"[skills] github install of '{skill_name}' from {canonical_url} rejected: {error}")
                    results.append(InstallResult(status="failed", skill=None, skill_name=skill_name, error=error))
            return results
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    async def install_from_marketplace(
        self,
        skill_id: str,
        version: Optional[str] = None,
        marketplace_source=None,
        _dep_chain: Optional[set] = None,
    ) -> InstallResult:
        """Install from the marketplace registry (hash-verified, no re-scan —
        the package was scanned at publish time). Missing manifest
        dependencies are installed recursively from the marketplace first.
        """
        source = marketplace_source or self._default_marketplace_source()
        dep_chain = _dep_chain or set()
        if skill_id in dep_chain:
            raise ValueError(f"Circular skill dependency detected at '{skill_id}'")
        dep_chain = dep_chain | {skill_id}
        if len(dep_chain) > 4:
            raise ValueError("Skill dependency chain exceeds the depth limit (3)")

        temp_dir = Path(tempfile.mkdtemp())
        try:
            zip_path, entry = await source.resolve_and_download(skill_id, temp_dir, version)

            actual_hash = _sha256_file(zip_path)
            if entry.package_hash and actual_hash != entry.package_hash:
                raise ValueError(
                    f"Package hash mismatch for '{skill_id}' — the artifact does not "
                    "match the registry record (possible tampering). Install aborted."
                )

            skill_root = self.skill_module.extract_skill_package(zip_path, temp_dir / "staged")

            # Recursive dependency install (marketplace source only).
            manifest = self._read_manifest(skill_root)
            installed = {s.name for s in self.skill_module.list_skills(include_disabled=True)}
            for dep in sorted((manifest or {}).get("dependencies") or {}):
                if dep not in installed:
                    await self.install_from_marketplace(
                        dep, marketplace_source=source, _dep_chain=dep_chain
                    )

            result = await self._install_staged(
                skill_root,
                source_type="marketplace",
                source_url=None,
                package_hash=actual_hash,
                skip_scan=True,
                catalog_skill_id=entry.skill_id,
            )
            if result.status == "installed":
                try:
                    await source.record_install(entry)
                except Exception as exc:
                    logger.debug(f"Download-counter update failed for '{skill_id}': {exc}")
            return result
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    def _default_marketplace_source(self):
        from narranexus.platform.utils.deployment_mode import get_deployment_mode

        from .registry import LocalMarketplaceSource, RegistryService, RemoteMarketplaceSource

        if get_deployment_mode() == "cloud":
            if self._db_client is None:
                raise ValueError(
                    "Marketplace install on cloud requires a db_client for the registry"
                )
            return LocalMarketplaceSource(RegistryService(self._db_client))
        return RemoteMarketplaceSource()

    async def uninstall(self, skill_name: str) -> bool:
        """Remove a skill and record the uninstall in the audit trail.

        Raises ValueError for built-in skills (propagated from SkillModule).
        """
        removed = self.skill_module.remove_skill(skill_name)
        if removed:
            await self._audit(skill_name, status="uninstalled", last_event="uninstall")
        return removed

    # -- pipeline body -------------------------------------------------------

    async def _install_staged(
        self,
        skill_root: Path,
        source_type: str,
        source_url: Optional[str],
        package_hash: Optional[str],
        original_zip_path: Optional[Path] = None,
        branch: Optional[str] = None,
        skip_scan: bool = False,
        catalog_skill_id: Optional[str] = None,
    ) -> InstallResult:
        from narranexus.platform.utils.file_safety import sanitize_filename

        manifest = self._read_manifest(skill_root)
        incoming = self.skill_module.parse_skill_package(skill_root)
        incoming_version = (manifest or {}).get("version") or incoming.version
        # The on-disk directory name MUST match the marketplace catalog id, or
        # everything keyed on that id (installed flags, dependency resolution,
        # update checks, audit rows) treats the skill as never-installed. When
        # a catalog id is known (marketplace source) it is authoritative;
        # otherwise fall back to the SKILL.md name. install_from_dir lands the
        # skill under this (sanitized) name.
        incoming_name = sanitize_filename(
            catalog_skill_id or incoming.name, label="skill name"
        )
        # Same fence the remove/disable/enable side applies (2026-10-28): a
        # dot-led name must not be installable, or it becomes installed-but-
        # undeletable. sanitize_filename alone lets dot-led names through.
        try:
            safe_id_segment(incoming_name, label="skill name")
        except ValueError as e:
            raise ValueError(str(e)) from e

        # Step 4.5 — security scan gate. Unvetted sources (zip/github/url)
        # are scanned here; marketplace packages were scanned at publish time
        # and are hash-verified instead (skip_scan=True).
        warnings: List[Dict[str, Any]] = []
        if not skip_scan:
            report = scan_skill_dir(skill_root)
            if report.status == "rejected":
                rules = sorted({i.rule for i in report.issues if i.severity == "high"})
                raise ValueError(
                    f"Security scan rejected this skill package ({', '.join(rules)}). "
                    "See the scan report for the offending files."
                )
            warnings = [i.to_dict() for i in report.issues]

        # Step 1 — resolve dependencies (must already be installed).
        self._check_dependencies(manifest)

        # Step 2 — validate compatibility.
        self._check_compatibility(manifest)

        # Step 3 — conflict detection + config capture for migration.
        existing = self.skill_module.get_skill(incoming_name)
        replaced_version: Optional[str] = None
        migrated_env: Dict[str, str] = {}
        if existing:
            existing_meta = self.skill_module.read_skill_meta(existing.name)
            existing_version = existing_meta.get("version") or existing.version
            if incoming_version and existing_version and incoming_version == existing_version:
                return InstallResult(
                    status="already_installed",
                    skill=existing,
                    warnings=warnings,
                    config_required=existing.env_configured is False,
                )
            replaced_version = existing_version
            migrated_env = existing_meta.get("env_config", {}) or {}

        # Step 5 — commit to skills/ via the module's shared tail. Pin the
        # directory name to the catalog id when known.
        info = self.skill_module.install_from_dir(
            skill_root, source_type=source_type, source_url=source_url,
            target_dir_name=catalog_skill_id,
        )

        # Step 6 — config migration + requirement status.
        meta_fields: Dict[str, Any] = {"updated_at": _now()}
        if catalog_skill_id:
            meta_fields["skill_id"] = catalog_skill_id  # marketplace catalog key
        if incoming_version:
            meta_fields["version"] = incoming_version
        if migrated_env:
            meta_fields["env_config"] = migrated_env
        if package_hash:
            meta_fields["hash"] = package_hash
        skill_dir = Path(info.path)
        meta_fields["content_hash"] = compute_content_hash(skill_dir)
        self.skill_module.merge_skill_meta(info.name, meta_fields)

        refreshed = self.skill_module.get_skill(info.name) or info
        config_required = bool(refreshed.requires_env) and refreshed.env_configured is False

        # Step 7 — audit + auto-archive (never fail the install on these).
        await self._audit(
            info.name,
            status="installed",
            last_event="update" if replaced_version else "install",
            version=incoming_version,
            source_type=source_type,
            source_url=source_url,
            package_hash=package_hash,
        )
        await self._backup(source_type, info.name, source_url, original_zip_path, branch)

        return InstallResult(
            status="installed",
            skill=refreshed,
            warnings=warnings,
            config_required=config_required,
            replaced_version=replaced_version,
        )

    # -- steps ---------------------------------------------------------------

    def _order_roots_by_dependency(self, skill_roots: List[Path]) -> List[tuple[Path, str]]:
        """(root, skill name) pairs, siblings a root depends on placed before it.

        Only same-repo edges are ordered here; a dependency that is not in
        the repo is left to ``_check_dependencies`` (must already be
        installed). Ties and roots without a manifest keep name order; an
        unreadable manifest / SKILL.md is not an ordering error — the root
        keeps its slot and fails on its own inside the install loop.
        """
        named: List[tuple[Path, str, set]] = []
        for root in skill_roots:
            try:
                name = self.skill_module.parse_skill_package(root).name
            except Exception:  # noqa: BLE001 — the per-skill install reports it
                name = root.name
            try:
                deps = set(((self._read_manifest(root) or {}).get("dependencies") or {}).keys())
            except ValueError:
                deps = set()
            named.append((root, name, deps))
        in_repo = {name for _, name, _ in named}
        ordered: List[tuple[Path, str]] = []
        placed: set = set()
        pending = list(named)
        while pending:
            ready = [item for item in pending if not ((item[2] & in_repo) - placed)]
            if not ready:  # dependency cycle: keep name order, let the checks explain
                ready = [pending[0]]
            for item in ready:
                ordered.append((item[0], item[1]))
                placed.add(item[1])
                pending.remove(item)
        return ordered

    @staticmethod
    def _read_manifest(skill_root: Path) -> Optional[dict]:
        manifest_file = skill_root / "manifest.json"
        if not manifest_file.exists():
            return None
        try:
            data = json.loads(manifest_file.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else None
        except (ValueError, OSError) as exc:
            raise ValueError(f"Invalid skill package: manifest.json is unreadable ({exc})")

    def _check_dependencies(self, manifest: Optional[dict]) -> None:
        dependencies = (manifest or {}).get("dependencies") or {}
        if not dependencies:
            return
        installed = {s.name for s in self.skill_module.list_skills(include_disabled=True)}
        missing = sorted(dep for dep in dependencies if dep not in installed)
        if missing:
            raise ValueError(
                f"Missing skill dependencies: {', '.join(missing)}. "
                "Install them first, then retry."
            )

    @staticmethod
    def _check_compatibility(manifest: Optional[dict]) -> None:
        compatibility = (manifest or {}).get("compatibility") or {}
        current = _current_app_version()
        if not compatibility or not current:
            return
        current_v = _version_tuple(current)
        minimum = compatibility.get("narranexus_min")
        maximum = compatibility.get("narranexus_max")
        if minimum and current_v < _version_tuple(minimum):
            raise ValueError(
                f"Skill requires NarraNexus >= {minimum} (current: {current})."
            )
        if maximum and current_v > _version_tuple(maximum):
            raise ValueError(
                f"Skill requires NarraNexus <= {maximum} (current: {current})."
            )

    async def _audit(self, skill_id: str, *, status: str, last_event: str, **fields) -> None:
        try:
            from narranexus.platform.repository.skill_installation_repository import (
                SkillInstallationRepository,
            )

            repo = SkillInstallationRepository(await self._get_db())
            if status == "installed":
                await repo.upsert_event(
                    self.agent_id,
                    self.user_id,
                    skill_id,
                    version=fields.get("version"),
                    source_type=fields.get("source_type", "manual"),
                    source_url=fields.get("source_url"),
                    package_hash=fields.get("package_hash"),
                    status=status,
                    last_event=last_event,
                )
            else:
                marked = await repo.mark_status(
                    self.agent_id, self.user_id, skill_id, status=status, last_event=last_event
                )
                if not marked:
                    logger.debug(f"No audit row to mark for '{skill_id}' ({status})")

            # A skill is a capability, and capabilities are what peers search
            # on: keep the agent's discovery row in step with what it can now
            # do, instead of waiting for its next turn (P1 section 02, target 1).
            from narranexus.platform.message_bus.agent_discovery_sync import (
                sync_agent_discovery,
            )
            await sync_agent_discovery(await self._get_db(), self.agent_id)
        except Exception as exc:
            # Disk is the source of truth — an audit failure must never fail
            # or roll back the filesystem operation. The reconciler heals it.
            logger.error(f"Skill audit write failed for '{skill_id}': {exc}")

    async def _backup(
        self,
        source_type: str,
        skill_name: str,
        source_url: Optional[str],
        original_zip_path: Optional[Path],
        branch: Optional[str],
    ) -> None:
        try:
            from narranexus.platform.bundle.skill_backup import backup_after_api_install

            await backup_after_api_install(
                user_id=self.user_id,
                skill_name=skill_name,
                source_type=source_type,
                source_url=source_url,
                original_zip_path=original_zip_path,
                branch=branch or "main",
            )
        except Exception as exc:
            logger.warning(f"Skill auto-archive failed for '{skill_name}': {exc}")

    async def _get_db(self):
        if self._db_client is None:
            from narranexus.platform.utils.db.db_factory import get_db_client

            self._db_client = await get_db_client()
        return self._db_client
