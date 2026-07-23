"""
@file_name: registry.py
@author: NetMind.AI
@date: 2026-07-21
@description: Marketplace registry — publish pipeline + catalog queries +
the two install sources (local DB registry vs remote cloud API).

The DB catalog (skill_catalog) is the ONLY directory truth (spec §4; the
v1.0 S3 registry-index.json was dropped). Artifacts live in the
ArtifactStore at "{skill_id}/{version}/{skill_id}-{version}.zip".
"""

import hashlib
import json
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from contextlib import asynccontextmanager

import httpx
from loguru import logger

from xyz_agent_context.repository.skill_catalog_repository import SkillCatalogRepository
from xyz_agent_context.repository.skill_scan_result_repository import (
    SkillScanResultRepository,
)
from xyz_agent_context.schema.skill_marketplace_schema import (
    SkillCatalogEntry,
    SkillScanResult,
)

from .artifact_store import ArtifactStore, get_artifact_store
from .scanner import ScanReport, scan_skill_dir


class PublishRejectedError(ValueError):
    """Publish failed the security gate; .report carries the scan detail."""

    def __init__(self, report: ScanReport):
        rules = sorted({i.rule for i in report.issues if i.severity == "high"})
        super().__init__(f"Security scan rejected this skill package ({', '.join(rules)}).")
        self.report = report


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def artifact_key(skill_id: str, version: str) -> str:
    return f"{skill_id}/{version}/{skill_id}-{version}.zip"


class RegistryService:
    """Cloud-side registry: publish, search, detail, updates, download."""

    def __init__(self, db_client, store: Optional[ArtifactStore] = None):
        self._db = db_client
        self.store = store or get_artifact_store()
        self.catalog = SkillCatalogRepository(db_client)
        self.scans = SkillScanResultRepository(db_client)

    # -- publish -------------------------------------------------------------

    async def publish(self, zip_path: Path, publisher: str) -> SkillCatalogEntry:
        """Validate -> scan gate -> upload artifact -> write catalog + scan rows."""
        from xyz_agent_context.module.skill_module import SkillModule

        staging_module = SkillModule(agent_id="__registry__", user_id=None)
        temp_dir = Path(tempfile.mkdtemp())
        try:
            skill_root = staging_module.extract_skill_package(zip_path, temp_dir)
            manifest = self._load_manifest(skill_root, staging_module)

            report = scan_skill_dir(skill_root)
            if report.status == "rejected":
                raise PublishRejectedError(report)

            skill_id = manifest["id"]
            version = manifest["version"]
            key = artifact_key(skill_id, version)
            package_hash = _sha256_file(zip_path)

            self.store.put_file(key, zip_path)
            self.store.put_bytes(
                f"{skill_id}/{version}/manifest.json",
                json.dumps(manifest, separators=(",", ":"), ensure_ascii=False).encode("utf-8"),
            )

            entry = SkillCatalogEntry(
                skill_id=skill_id,
                version=version,
                name=manifest.get("name") or skill_id,
                description=manifest.get("description"),
                author=manifest.get("author"),
                license=manifest.get("license"),
                category=manifest.get("category"),
                capabilities=manifest.get("capabilities") or [],
                tags=manifest.get("tags") or [],
                config_schema=manifest.get("config_schema"),
                dependencies=manifest.get("dependencies") or {},
                compatibility=manifest.get("compatibility"),
                s3_key=key,
                package_hash=package_hash,
                publisher=publisher,
                is_default=bool(manifest.get("default")),
                scan_status=report.status,
                status="published",
                published_at=_utcnow(),  # type: ignore[arg-type]  # pydantic coerces str -> datetime
            )
            published = await self.catalog.publish(entry)
            await self.scans.record(
                SkillScanResult(
                    skill_id=skill_id,
                    version=version,
                    status=report.status,
                    high_issues=report.high_issues,
                    low_issues=report.low_issues,
                    issues=[i.to_dict() for i in report.issues],
                    scanner_version=report.scanner_version,
                )
            )
            logger.info(f"Published skill {skill_id}@{version} by {publisher} ({report.status})")
            return published
        finally:
            import shutil

            if temp_dir.exists():
                shutil.rmtree(temp_dir)

    @staticmethod
    def _load_manifest(skill_root: Path, staging_module) -> Dict[str, Any]:
        """manifest.json is authoritative; synthesize a minimal one from
        SKILL.md frontmatter when absent. id/version are mandatory."""
        manifest_file = skill_root / "manifest.json"
        manifest: Dict[str, Any] = {}
        if manifest_file.exists():
            try:
                loaded = json.loads(manifest_file.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    manifest = loaded
            except ValueError as exc:
                raise ValueError(f"Invalid manifest.json: {exc}")

        info = staging_module.parse_skill_package(skill_root)
        from xyz_agent_context.utils.file_safety import sanitize_filename

        manifest.setdefault("id", sanitize_filename(info.name, label="skill id"))
        manifest.setdefault("name", info.name)
        manifest.setdefault("description", info.description)
        if info.version and "version" not in manifest:
            manifest["version"] = info.version

        if not manifest.get("version"):
            raise ValueError(
                "A version is required to publish: set `version` in manifest.json "
                "or in the SKILL.md frontmatter."
            )
        return manifest

    # -- queries -------------------------------------------------------------

    async def search(self, **kwargs) -> Tuple[List[SkillCatalogEntry], int]:
        return await self.catalog.search(**kwargs)

    async def get_detail(
        self, skill_id: str, version: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        # `entry` must describe the REQUESTED version (its own package_hash),
        # not always the latest — otherwise a version-pinned install verifies
        # the downloaded bytes against the wrong hash and fails as tampering.
        entry = (
            await self.catalog.get_version(skill_id, version)
            if version
            else await self.catalog.get_latest(skill_id)
        )
        if entry is None:
            return None
        versions = await self.catalog.list_versions(skill_id)
        scan = await self.scans.latest_for(skill_id, entry.version)
        return {
            "entry": entry.model_dump(),
            "versions": [
                {"version": v.version, "status": v.status, "published_at": v.published_at}
                for v in versions
            ],
            "scan": scan.model_dump() if scan else None,
        }

    async def check_updates(self, installed: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """installed: [{skill_id, version}] -> entries with a newer published version."""
        from xyz_agent_context.repository.skill_catalog_repository import _semver_key

        updates = []
        for item in installed:
            skill_id, version = item.get("skill_id"), item.get("version")
            if not skill_id or not version:
                continue
            latest = await self.catalog.get_latest(skill_id)
            if latest and _semver_key(latest.version) > _semver_key(version):
                updates.append(
                    {
                        "skill_id": skill_id,
                        "installed_version": version,
                        "latest_version": latest.version,
                        "description": latest.description,
                    }
                )
        return updates

    async def download_to(
        self, skill_id: str, dest_dir: Path, version: Optional[str] = None
    ) -> Tuple[Path, SkillCatalogEntry]:
        entry = (
            await self.catalog.get_version(skill_id, version)
            if version
            else await self.catalog.get_latest(skill_id)
        )
        if entry is None:
            raise FileNotFoundError(f"Skill '{skill_id}' not found in the marketplace")
        dest = Path(dest_dir) / f"{entry.skill_id}-{entry.version}.zip"
        self.store.get_to_path(entry.s3_key, dest)
        return dest, entry


# ---------------------------------------------------------------------------
# Install sources (consumed by InstallPipeline.install_from_marketplace)
# ---------------------------------------------------------------------------


class LocalMarketplaceSource:
    """Cloud deployment: the registry lives in this process's DB."""

    def __init__(self, registry: RegistryService):
        self.registry = registry

    async def resolve_and_download(
        self, skill_id: str, dest_dir: Path, version: Optional[str] = None
    ) -> Tuple[Path, SkillCatalogEntry]:
        return await self.registry.download_to(skill_id, dest_dir, version)

    async def record_install(self, entry: SkillCatalogEntry) -> None:
        await self.registry.catalog.increment_downloads(entry.skill_id, entry.version)


class RemoteMarketplaceSource:
    """Desktop deployment: the registry is the cloud API."""

    def __init__(self, base_url: Optional[str] = None, client: Optional[httpx.AsyncClient] = None):
        import os

        self.base_url = (
            base_url
            or os.environ.get("NARRANEXUS_MARKETPLACE_URL", "https://agent.narra.nexus")
        ).rstrip("/")
        self._client = client

    @asynccontextmanager
    async def _http(self):
        """Yield an httpx client, closing an internally-created one so the
        connection pool never leaks (RemoteMarketplaceSource is created per
        service call). An injected client (tests) is left for the caller to
        own."""
        if self._client is not None:
            yield self._client
        else:
            async with httpx.AsyncClient(base_url=self.base_url, timeout=60.0) as client:
                yield client

    async def resolve_and_download(
        self, skill_id: str, dest_dir: Path, version: Optional[str] = None
    ) -> Tuple[Path, SkillCatalogEntry]:
        params = {"version": version} if version else {}
        async with self._http() as client:
            # Fetch the detail FOR THE REQUESTED VERSION so entry_data carries
            # that version's own package_hash. (Never rewrite version onto a
            # latest-entry — that decouples version from hash and the pipeline
            # then rejects the download as tampered.)
            detail = await client.get(
                f"/api/marketplace/skills/{skill_id}", params=params
            )
            if detail.status_code == 404:
                raise FileNotFoundError(f"Skill '{skill_id}' not found in the marketplace")
            detail.raise_for_status()
            entry_data = detail.json()["entry"]

            response = await client.get(
                f"/api/marketplace/skills/{skill_id}/download", params=params
            )
            if response.status_code == 404:
                raise FileNotFoundError(f"Skill '{skill_id}'@{version or 'latest'} has no artifact")
            response.raise_for_status()
        # Integrity: the expected hash comes ONLY from the catalog detail
        # (entry_data), NEVER from the download response's own X-Package-Hash
        # header — whoever can serve a tampered artifact can serve a matching
        # header, so trusting it means no verification at all. The pipeline
        # verifies the downloaded bytes against THIS hash.
        entry = SkillCatalogEntry(**entry_data)
        dest = Path(dest_dir) / f"{entry.skill_id}-{entry.version}.zip"
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(response.content)
        return dest, entry

    async def record_install(self, entry: SkillCatalogEntry) -> None:
        # The cloud increments the download counter when serving /download.
        return None

    async def search(self, params: Dict[str, Any]) -> Dict[str, Any]:
        async with self._http() as client:
            response = await client.get("/api/marketplace/skills/search", params=params)
            response.raise_for_status()
            return response.json()

    async def get_detail(
        self, skill_id: str, version: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        params = {"version": version} if version else {}
        async with self._http() as client:
            response = await client.get(
                f"/api/marketplace/skills/{skill_id}", params=params
            )
            if response.status_code == 404:
                return None
            response.raise_for_status()
            return response.json()

    async def check_updates(self, installed: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        spec = ",".join(f"{i['skill_id']}@{i['version']}" for i in installed if i.get("version"))
        if not spec:
            return []
        async with self._http() as client:
            response = await client.get("/api/marketplace/skills/updates", params={"skills": spec})
        response.raise_for_status()
        return response.json().get("updates", [])

    async def list_defaults(self) -> List[Dict[str, Any]]:
        async with self._http() as client:
            response = await client.get("/api/marketplace/skills/defaults")
        response.raise_for_status()
        return response.json().get("items", [])


def _utcnow() -> str:
    from datetime import datetime, timezone

    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
