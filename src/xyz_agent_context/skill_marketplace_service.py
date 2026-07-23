"""
@file_name: skill_marketplace_service.py
@author: NetMind.AI
@date: 2026-07-21
@description: Service protocol layer for the Skill Marketplace.

Public façade used by backend routes and MCP tools. Hides the deployment
split: on cloud the registry is this process's DB + artifact store; on
desktop it is the cloud API (RemoteMarketplaceSource). Installs always run
locally through the InstallPipeline against this host's workspace.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from loguru import logger

from xyz_agent_context._skill_marketplace_impl.install_pipeline import (
    InstallPipeline,
    InstallResult,
)
from xyz_agent_context._skill_marketplace_impl.registry import (
    PublishRejectedError,
    RegistryService,
    RemoteMarketplaceSource,
)
from xyz_agent_context.module.skill_module import SkillModule
from xyz_agent_context.utils.deployment_mode import get_deployment_mode

__all__ = ["SkillMarketplaceService", "PublishRejectedError"]


class SkillMarketplaceService:
    """One instance per request/tool-call; cheap to construct."""

    def __init__(self, db_client=None):
        self._db_client = db_client

    # -- registry access -----------------------------------------------------

    def _is_registry_host(self) -> bool:
        """Cloud deployments ARE the registry. A local/desktop deployment can
        opt into hosting its own registry (dev, offline demos, or before the
        cloud marketplace is live) via SKILL_MARKETPLACE_LOCAL_REGISTRY=1 —
        it then serves/browses its own catalog instead of proxying to
        NARRANEXUS_MARKETPLACE_URL."""
        import os

        if os.environ.get("SKILL_MARKETPLACE_LOCAL_REGISTRY", "").lower() in ("1", "true"):
            return True
        from xyz_agent_context.settings import settings

        if settings.skill_marketplace_local_registry:
            return True
        return get_deployment_mode() == "cloud"

    async def _registry(self) -> RegistryService:
        return RegistryService(await self._get_db())

    def _remote(self) -> RemoteMarketplaceSource:
        return RemoteMarketplaceSource()

    async def _get_db(self):
        if self._db_client is None:
            from xyz_agent_context.utils.db_factory import get_db_client

            self._db_client = await get_db_client()
        return self._db_client

    # -- queries -------------------------------------------------------------

    async def search(
        self,
        q: Optional[str] = None,
        category: Optional[str] = None,
        capability: Optional[str] = None,
        tags: Optional[List[str]] = None,
        sort: str = "downloads",
        page: int = 1,
        limit: int = 20,
        agent_id: Optional[str] = None,
        user_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        if self._is_registry_host():
            registry = await self._registry()
            items, total = await registry.search(
                q=q, category=category, capability=capability,
                tags=tags, sort=sort, page=page, limit=limit,
            )
            payload = {
                "items": [e.model_dump() for e in items],
                "total": total,
                "page": page,
                "limit": limit,
            }
        else:
            params: Dict[str, Any] = {"sort": sort, "page": page, "limit": limit}
            if q:
                params["q"] = q
            if category:
                params["category"] = category
            if capability:
                params["capability"] = capability
            if tags:
                params["tags"] = ",".join(tags)
            payload = await self._remote().search(params)

        if agent_id and user_id:
            self._annotate_installed(payload.get("items", []), agent_id, user_id)
        return payload

    def _annotate_installed(self, items: List[Dict[str, Any]], agent_id: str, user_id: str) -> None:
        from xyz_agent_context.repository.skill_catalog_repository import _semver_key

        module = SkillModule(agent_id=agent_id, user_id=user_id)
        installed_versions: Dict[str, Optional[str]] = {}
        for skill in module.list_skills(include_disabled=True):
            meta = module.read_skill_meta(skill.name)
            # Key on the marketplace catalog id (stored in .skill_meta.json at
            # install), falling back to the SKILL.md name. Without this a skill
            # whose catalog id differs from its SKILL.md name shows as never
            # installed in the marketplace.
            key = meta.get("skill_id") or skill.name
            installed_versions[key] = meta.get("version") or skill.version
        for item in items:
            skill_id = item.get("skill_id") or item.get("id") or ""
            item["installed"] = bool(skill_id) and skill_id in installed_versions
            current = installed_versions.get(skill_id)
            item["update_available"] = bool(
                item["installed"]
                and current
                and item.get("version")
                and _semver_key(item["version"]) > _semver_key(current)
            )

    async def get_detail(
        self, skill_id: str, version: Optional[str] = None
    ) -> Optional[Dict[str, Any]]:
        if self._is_registry_host():
            return await (await self._registry()).get_detail(skill_id, version)
        return await self._remote().get_detail(skill_id, version)

    async def check_updates(self, agent_id: str, user_id: str) -> List[Dict[str, Any]]:
        module = SkillModule(agent_id=agent_id, user_id=user_id)
        installed = []
        for skill in module.list_skills(include_disabled=True):
            meta = module.read_skill_meta(skill.name)
            version = meta.get("version") or skill.version
            if version:
                installed.append(
                    {"skill_id": meta.get("skill_id") or skill.name, "version": version}
                )
        if not installed:
            return []
        if self._is_registry_host():
            return await (await self._registry()).check_updates(installed)
        return await self._remote().check_updates(installed)

    async def check_updates_for(self, installed: List[Dict[str, str]]) -> List[Dict[str, Any]]:
        """Registry-host-side batch update check (serves GET /updates?skills=)."""
        return await (await self._registry()).check_updates(installed)

    async def list_defaults(self) -> List[Dict[str, Any]]:
        if self._is_registry_host():
            registry = await self._registry()
            return [e.model_dump() for e in await registry.catalog.list_defaults()]
        return await self._remote().list_defaults()

    async def install_defaults(self, agent_id: str, user_id: str) -> Dict[str, Any]:
        """Install every is_default skill into a (typically new) workspace.

        Per-skill failures are collected, never raised — default-skill setup
        must not break agent creation, and an unreachable registry (desktop
        offline) degrades to a no-op.
        """
        installed, skipped, failed = [], [], []
        try:
            defaults = await self.list_defaults()
        except Exception as exc:
            logger.warning(f"Default skills: registry unreachable, skipping ({exc})")
            return {"installed": [], "skipped": [], "failed": [], "registry_unreachable": True}

        for entry in defaults:
            skill_id = entry.get("skill_id")
            if not skill_id:
                continue
            try:
                result = await self.install(agent_id, user_id, skill_id)
                (installed if result.status == "installed" else skipped).append(skill_id)
            except Exception as exc:
                logger.warning(f"Default skill '{skill_id}' install failed: {exc}")
                failed.append(skill_id)
        if installed:
            logger.info(
                f"Default skills installed for agent={agent_id}: {', '.join(installed)}"
            )
        return {"installed": installed, "skipped": skipped, "failed": failed}

    # -- mutations -----------------------------------------------------------

    async def install(
        self, agent_id: str, user_id: str, skill_id: str, version: Optional[str] = None
    ) -> InstallResult:
        from xyz_agent_context._skill_marketplace_impl.registry import LocalMarketplaceSource

        # The mode decision lives HERE (single place): cloud installs read the
        # local DB registry; desktop installs pull from the cloud API.
        if self._is_registry_host():
            source = LocalMarketplaceSource(await self._registry())
        else:
            source = self._remote()
        pipeline = InstallPipeline(agent_id, user_id, db_client=await self._get_db())
        return await pipeline.install_from_marketplace(
            skill_id, version=version, marketplace_source=source
        )

    async def install_from_url(
        self, agent_id: str, user_id: str, url: str, branch: str = "main"
    ) -> InstallResult:
        pipeline = InstallPipeline(agent_id, user_id, db_client=await self._get_db())
        return await pipeline.install_from_github(url, branch=branch)

    async def uninstall(self, agent_id: str, user_id: str, skill_name: str) -> bool:
        pipeline = InstallPipeline(agent_id, user_id, db_client=await self._get_db())
        return await pipeline.uninstall(skill_name)

    async def publish(self, zip_path: Path, publisher: str):
        if not self._is_registry_host():
            logger.warning("Publish invoked on a non-cloud host; writing to the local registry")
        return await (await self._registry()).publish(zip_path, publisher)

    async def download_to(self, skill_id: str, dest_dir: Path, version: Optional[str] = None):
        registry = await self._registry()
        path, entry = await registry.download_to(skill_id, dest_dir, version)
        await registry.catalog.increment_downloads(entry.skill_id, entry.version)
        return path, entry
