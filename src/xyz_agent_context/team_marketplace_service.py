"""
@file_name: team_marketplace_service.py
@author: NetMind.AI
@date: 2026-07-21
@description: Service protocol layer for the Team Marketplace.

Public façade for backend routes. Hides the deployment split (spec §5):
- Browse/detail: cloud reads its DB catalog; desktop proxies the cloud API.
- Install: ALWAYS runs the LOCAL bundle importer against the LOCAL DB (fork
  lands in this backend's own agents/teams). Only the "get the .nxbundle
  bytes" step diverges — the registry host reads its artifact store directly;
  a desktop client HTTP-downloads the cloud blob. This mirrors the skill
  marketplace's Local/Remote source abstraction.

Publish/upload stores the blob in get_template_store() (own S3 prefix /
local subfolder, separate from skills) and writes a catalog row.
"""

import hashlib
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx
from loguru import logger

from xyz_agent_context._skill_marketplace_impl.artifact_store import (
    ArtifactStore,
    get_template_store,
)
from xyz_agent_context.repository.team_catalog_repository import TeamCatalogRepository
from xyz_agent_context.schema.team_marketplace_schema import TeamTemplate
from xyz_agent_context.utils.deployment_mode import get_deployment_mode

__all__ = ["TeamMarketplaceService"]


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def store_key_for(template_id: str, sha256: str) -> str:
    return f"{template_id}/{sha256[:8]}/{template_id}.nxbundle"


class TeamMarketplaceService:
    def __init__(self, db_client=None, store: Optional[ArtifactStore] = None):
        self._db_client = db_client
        self._store = store

    # -- plumbing ------------------------------------------------------------

    def _is_registry_host(self) -> bool:
        import os

        if os.environ.get("SKILL_MARKETPLACE_LOCAL_REGISTRY", "").lower() in ("1", "true"):
            return True
        from xyz_agent_context.settings import settings

        if getattr(settings, "skill_marketplace_local_registry", False):
            return True
        return get_deployment_mode() == "cloud"

    async def _get_db(self):
        if self._db_client is None:
            from xyz_agent_context.utils.db_factory import get_db_client

            self._db_client = await get_db_client()
        return self._db_client

    async def _catalog(self) -> TeamCatalogRepository:
        return TeamCatalogRepository(await self._get_db())

    def _store_ref(self) -> ArtifactStore:
        if self._store is None:
            self._store = get_template_store()
        return self._store

    def _cloud_base(self) -> str:
        import os

        return os.environ.get(
            "NARRANEXUS_MARKETPLACE_URL", "https://agent.narra.nexus"
        ).rstrip("/")

    # -- queries -------------------------------------------------------------

    async def list_templates(self) -> Dict[str, Any]:
        if self._is_registry_host():
            rows = await (await self._catalog()).list_enabled()
            return {"templates": [t.model_dump() for t in rows]}
        async with httpx.AsyncClient(base_url=self._cloud_base(), timeout=30.0) as ac:
            resp = await ac.get("/api/marketplace/teams/templates")
            resp.raise_for_status()
            return resp.json()

    async def get_template(self, template_id: str) -> Optional[Dict[str, Any]]:
        if self._is_registry_host():
            t = await (await self._catalog()).get(template_id)
            return t.model_dump() if (t and t.enabled) else None
        async with httpx.AsyncClient(base_url=self._cloud_base(), timeout=30.0) as ac:
            resp = await ac.get(f"/api/marketplace/teams/templates/{template_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()

    # -- install (local importer, byte source depends on deployment) ---------

    async def resolve_bundle(self, template_id: str, dest_dir: Path) -> Path:
        """Obtain the .nxbundle for this template and return its local path.

        Registry host: read the artifact store directly. Desktop client:
        HTTP-download from the cloud registry's /download endpoint. sha256 is
        verified by the caller (install_preflight) in both cases."""
        dest = Path(dest_dir) / f"{template_id}.nxbundle"
        if self._is_registry_host():
            entry = await (await self._catalog()).get(template_id)
            if entry is None or not entry.enabled:
                raise FileNotFoundError(f"Team template '{template_id}' not found")
            self._store_ref().get_to_path(entry.store_key, dest)
            return dest
        # Desktop client: real cross-machine fetch from the cloud registry.
        async with httpx.AsyncClient(base_url=self._cloud_base(), timeout=120.0) as ac:
            resp = await ac.get(f"/api/marketplace/teams/templates/{template_id}/download")
            if resp.status_code == 404:
                raise FileNotFoundError(f"Team template '{template_id}' not found")
            resp.raise_for_status()
            dest.write_bytes(resp.content)
        return dest

    async def expected_sha256(self, template_id: str) -> Optional[str]:
        if self._is_registry_host():
            entry = await (await self._catalog()).get(template_id)
            return entry.bundle_sha256 if entry else None
        detail = await self.get_template(template_id)
        return (detail or {}).get("bundle_sha256")

    async def install_preflight(self, template_id: str, user_id: str) -> Dict[str, Any]:
        """Fetch the bundle (store or cloud), verify sha256, run the LOCAL
        importer preflight. Returns the same shape as /api/bundle/import/
        preflight so the frontend reuses the review UI; caller then confirms
        via /api/bundle/import/confirm."""
        from xyz_agent_context.bundle.importer import preflight

        tmp = Path(tempfile.mkdtemp(prefix="nx-team-install-"))
        try:
            path = await self.resolve_bundle(template_id, tmp)
            expected = await self.expected_sha256(template_id)
            if expected:
                actual = _sha256_file(path)
                if actual.lower() != expected.lower():
                    raise ValueError(
                        f"Bundle sha256 mismatch for '{template_id}' — the "
                        "artifact does not match the catalog record (possible "
                        "tampering). Install aborted."
                    )
            result = await preflight(path, user_id)
            # Best-effort download counter (registry host only; desktop counts
            # server-side when it serves /download).
            if self._is_registry_host():
                try:
                    await (await self._catalog()).increment_downloads(template_id)
                except Exception as exc:  # noqa: BLE001
                    logger.debug(f"team template download counter failed: {exc}")
            return result
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    async def get_bundle_bytes(self, template_id: str) -> tuple[bytes, str]:
        """Registry-host only: raw .nxbundle bytes + sha256 for the /download
        endpoint (desktop clients pull through this)."""
        entry = await (await self._catalog()).get(template_id)
        if entry is None or not entry.enabled:
            raise FileNotFoundError(f"Team template '{template_id}' not found")
        tmp = Path(tempfile.mkdtemp(prefix="nx-team-dl-"))
        try:
            dest = tmp / "bundle.nxbundle"
            self._store_ref().get_to_path(entry.store_key, dest)
            await (await self._catalog()).increment_downloads(template_id)
            return dest.read_bytes(), entry.bundle_sha256
        finally:
            import shutil

            shutil.rmtree(tmp, ignore_errors=True)

    # -- publish (registry host / ops) ---------------------------------------

    async def publish(
        self,
        bundle_path: Path,
        *,
        template_id: str,
        name: str,
        description: str = "",
        categories: Optional[List[str]] = None,
        author: str = "NarraNexus team",
        agent_count: int = 1,
        thumbnail_url: Optional[str] = None,
        sort_order: int = 0,
    ) -> TeamTemplate:
        sha = _sha256_file(bundle_path)
        key = store_key_for(template_id, sha)
        self._store_ref().put_file(key, bundle_path)
        template = TeamTemplate(
            template_id=template_id,
            name=name,
            description=description,
            categories=categories or [],
            author=author,
            agent_count=agent_count,
            thumbnail_url=thumbnail_url,
            store_key=key,
            bundle_sha256=sha,
            enabled=True,
            sort_order=sort_order,
        )
        result = await (await self._catalog()).save_template(template)
        logger.info(f"Published team template {template_id} ({agent_count} agents, {sha[:12]})")
        return result

    async def delete(self, template_id: str) -> bool:
        return bool(await (await self._catalog()).remove(template_id))
