"""
@file_name: marketplace_skills.py
@author: NetMind.AI
@date: 2026-07-21
@description: /api/marketplace/skills/* — Skill Marketplace API.

The marketplace namespace is split by object: this router owns
/api/marketplace/skills/*; /api/marketplace/teams/* is reserved for the
Team/Agent bundle marketplace and must not be claimed here.

Read endpoints (search/detail/updates-by-spec/download) are public like a
package registry; workspace-touching endpoints (install, agent-scoped
search annotations, agent-scoped updates) resolve identity via
auth_middleware. Publish is gated by MARKETPLACE_PUBLISH_TOKEN.
"""

import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, File, Form, Header, HTTPException, Query, Request, UploadFile
from fastapi.responses import FileResponse
from loguru import logger

from backend.auth import (
    reject_cross_origin,
    resolve_current_user_id,
    resolve_optional_user_id,
)
from xyz_agent_context.skill_marketplace_service import (
    PublishRejectedError,
    SkillMarketplaceService,
)

router = APIRouter()


def _parse_skills_spec(spec: str):
    installed = []
    for token in spec.split(","):
        token = token.strip()
        if "@" in token:
            skill_id, version = token.rsplit("@", 1)
            if skill_id and version:
                installed.append({"skill_id": skill_id, "version": version})
    return installed


@router.get("/search")
async def search_skills(
    request: Request,
    q: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    capability: Optional[str] = Query(None),
    tags: Optional[str] = Query(None, description="Comma-separated tag filter"),
    sort: str = Query("downloads", pattern="^(downloads|published|name)$"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    agent_id: Optional[str] = Query(None, description="Annotate installed/update_available"),
):
    # Optional identity: anonymous searches (desktop → cloud registry) are
    # served without the installed/update_available annotations.
    user_id = await resolve_optional_user_id(request) if agent_id else None
    try:
        return await SkillMarketplaceService().search(
            q=q,
            category=category,
            capability=capability,
            tags=[t for t in (tags or "").split(",") if t] or None,
            sort=sort,
            page=page,
            limit=limit,
            agent_id=agent_id,
            user_id=user_id,
        )
    except Exception as e:
        logger.exception(f"Marketplace search failed: {e}")
        raise HTTPException(status_code=500, detail="Marketplace search failed")


@router.get("/updates")
async def check_updates(
    request: Request,
    agent_id: Optional[str] = Query(None),
    skills: Optional[str] = Query(None, description="Batch spec: id@version,id@version"),
):
    """Two modes: ?skills= is the public registry-side batch check (used by
    desktop clients); ?agent_id= checks one workspace's installed skills."""
    try:
        service = SkillMarketplaceService()
        if skills:
            return {"updates": await service.check_updates_for(_parse_skills_spec(skills))}
        if agent_id:
            user_id = await resolve_current_user_id(request)
            return {"updates": await service.check_updates(agent_id, user_id)}
        raise HTTPException(status_code=400, detail="Provide either agent_id or skills")
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Marketplace update check failed: {e}")
        raise HTTPException(status_code=500, detail="Update check failed")


@router.get("/defaults")
async def list_default_skills():
    """Skills flagged is_default — auto-installed on agent creation.
    Public read (desktop clients fetch this anonymously)."""
    try:
        return {"items": await SkillMarketplaceService().list_defaults()}
    except Exception as e:
        logger.exception(f"Marketplace defaults listing failed: {e}")
        raise HTTPException(status_code=500, detail="Defaults listing failed")


@router.get("/{skill_id}/download")
async def download_skill(skill_id: str, version: Optional[str] = Query(None)):
    """Serve the artifact (registry host only). Increments the download counter."""
    temp_dir = Path(tempfile.mkdtemp())
    try:
        path, entry = await SkillMarketplaceService().download_to(skill_id, temp_dir, version)
    except FileNotFoundError:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=404, detail={"code": "SKILL_NOT_FOUND"})
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        logger.exception(f"Marketplace download failed: {e}")
        raise HTTPException(status_code=500, detail="Download failed")

    from starlette.background import BackgroundTask

    return FileResponse(
        path,
        media_type="application/zip",
        filename=path.name,
        headers={"X-Skill-Version": entry.version, "X-Package-Hash": entry.package_hash},
        background=BackgroundTask(shutil.rmtree, str(temp_dir), True),
    )


@router.get("/{skill_id}")
async def skill_detail(skill_id: str, version: Optional[str] = Query(None)):
    try:
        detail = await SkillMarketplaceService().get_detail(skill_id, version)
    except Exception as e:
        logger.exception(f"Marketplace detail failed: {e}")
        raise HTTPException(status_code=500, detail="Detail lookup failed")
    if detail is None:
        raise HTTPException(status_code=404, detail={"code": "SKILL_NOT_FOUND"})
    return detail


@router.post("/{skill_id}/install")
async def install_skill(skill_id: str, request: Request, body: Optional[dict] = None):
    body = body or {}
    agent_id = body.get("agent_id")
    if not agent_id:
        raise HTTPException(status_code=400, detail="agent_id is required")
    user_id = await resolve_current_user_id(request)

    try:
        result = await SkillMarketplaceService().install(
            agent_id, user_id, skill_id, version=body.get("version")
        )
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "SKILL_NOT_FOUND"})
    except ValueError as e:
        logger.warning(
            f"marketplace install rejected: agent_id={agent_id} user_id={user_id} "
            f"skill_id={skill_id} reason={e!s}"
        )
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Marketplace install failed: {e}")
        raise HTTPException(status_code=500, detail={"code": "INSTALL_FAILED"})

    if result.status == "already_installed":
        raise HTTPException(
            status_code=409,
            detail={"code": "SKILL_ALREADY_INSTALLED", "skill_id": skill_id},
        )
    return {
        "status": "installed",
        "skill_id": skill_id,
        "version": result.skill.version if result.skill else None,
        "needs_restart": True,
        "config_required": result.config_required,
        "warnings": result.warnings,
        "replaced_version": result.replaced_version,
    }


@router.post("/publish")
async def publish_skill(
    request: Request,
    file: UploadFile = File(...),
    publisher: str = Form("narranexus-team"),
    x_publish_token: Optional[str] = Header(None),
):
    expected = os.environ.get("MARKETPLACE_PUBLISH_TOKEN")
    if expected:
        if x_publish_token != expected:
            raise HTTPException(status_code=403, detail="Invalid or missing publish token")
    else:
        # No token configured: publishing stays CLOSED on cloud (multi-tenant),
        # but is allowed in local mode — the backend is loopback-bound and the
        # OS user is the security boundary (same trust model as the rest of
        # the local API). Lets dev/desktop hosts run their own registry.
        from xyz_agent_context.utils.deployment_mode import is_cloud_mode

        if is_cloud_mode():
            raise HTTPException(status_code=403, detail="Publishing is not enabled on this server")
        # Local no-token: loopback-trust only. Reject cross-origin (CSRF) —
        # a browser always sends Origin on a cross-site POST; the CLI sends
        # none. Without this a malicious page could POST to localhost:8000.
        reject_cross_origin(request)
        logger.info("Marketplace publish: local mode without token — allowed")

    temp_dir = Path(tempfile.mkdtemp())
    try:
        # NEVER trust the client-supplied filename — pathlib does not
        # normalize `..`, so `file.filename = "../../x"` would escape temp_dir
        # and write an arbitrary file. Fixed name; content is validated by the
        # scan gate downstream.
        zip_path = temp_dir / "upload.zip"
        zip_path.write_bytes(await file.read())
        entry = await SkillMarketplaceService().publish(zip_path, publisher)
        return {"status": "published", "skill_id": entry.skill_id, "version": entry.version,
                "scan_status": entry.scan_status}
    except PublishRejectedError as e:
        raise HTTPException(
            status_code=422,
            detail={
                "code": "SECURITY_SCAN_FAILED",
                "status": "rejected",
                "scan_report": [i.to_dict() for i in e.report.issues],
            },
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Marketplace publish failed: {e}")
        raise HTTPException(status_code=500, detail="Publish failed")
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)
