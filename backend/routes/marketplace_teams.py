"""
@file_name: marketplace_teams.py
@author: NetMind.AI
@date: 2026-07-21
@description: /api/marketplace/teams/* — Team Marketplace API.

The other half of the /api/marketplace namespace (skills/* is the sibling).
Browse/detail/download are public reads (desktop clients fetch anonymously).
Install-preflight resolves identity and runs the LOCAL bundle importer;
confirm reuses the existing POST /api/bundle/import/confirm. Publish/delete
are staff-gated on cloud, open in local mode (loopback + OS-user boundary),
mirroring the skill publish policy.
"""

import tempfile
from pathlib import Path
from typing import List, Optional

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import Response
from loguru import logger

from backend.auth import reject_cross_origin, resolve_current_user_id
from xyz_agent_context.team_marketplace_service import TeamMarketplaceService

router = APIRouter()


def _require_publisher(request: Request) -> None:
    """Cloud: staff role required. Local: loopback-trust, but reject a
    cross-origin (CSRF) POST — same guard as the skill publish endpoint."""
    from xyz_agent_context.utils.deployment_mode import is_cloud_mode

    if not is_cloud_mode():
        reject_cross_origin(request)
        return
    role = getattr(request.state, "role", None)
    if not getattr(request.state, "user_id", None):
        raise HTTPException(status_code=401, detail="Authentication required")
    if role != "staff":
        raise HTTPException(status_code=403, detail="staff role required")


@router.get("/templates")
async def list_templates():
    try:
        return await TeamMarketplaceService().list_templates()
    except Exception as e:
        logger.exception(f"Team marketplace list failed: {e}")
        raise HTTPException(status_code=500, detail="Team marketplace listing failed")


@router.get("/templates/{template_id}/download")
async def download_template(template_id: str):
    """Serve the .nxbundle from the store (registry host). Desktop clients
    pull through here."""
    try:
        data, sha = await TeamMarketplaceService().get_bundle_bytes(template_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "TEMPLATE_NOT_FOUND"})
    except Exception as e:
        logger.exception(f"Team template download failed: {e}")
        raise HTTPException(status_code=500, detail="Download failed")
    return Response(
        content=data,
        media_type="application/zip",
        headers={
            "X-Bundle-Sha256": sha,
            "Content-Disposition": f'attachment; filename="{template_id}.nxbundle"',
        },
    )


@router.get("/templates/{template_id}")
async def template_detail(template_id: str):
    try:
        detail = await TeamMarketplaceService().get_template(template_id)
    except Exception as e:
        logger.exception(f"Team template detail failed: {e}")
        raise HTTPException(status_code=500, detail="Detail lookup failed")
    if detail is None:
        raise HTTPException(status_code=404, detail={"code": "TEMPLATE_NOT_FOUND"})
    return detail


@router.post("/templates/{template_id}/install-preflight")
async def install_preflight(template_id: str, request: Request):
    """Resolve the bundle (store or cloud), verify sha256, run the LOCAL
    importer preflight. Returns the standard preflight payload; the frontend
    then confirms via POST /api/bundle/import/confirm."""
    user_id = await resolve_current_user_id(request)
    try:
        return await TeamMarketplaceService().install_preflight(template_id, user_id)
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail={"code": "TEMPLATE_NOT_FOUND"})
    except ValueError as e:
        logger.warning(f"team install-preflight rejected: {template_id} — {e}")
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Team install-preflight failed: {e}")
        raise HTTPException(status_code=500, detail="Install preflight failed")


@router.post("/templates")
async def publish_template(
    request: Request,
    file: UploadFile = File(...),
    template_id: str = Form(...),
    name: str = Form(...),
    description: str = Form(""),
    categories: str = Form(""),  # comma-separated
    author: str = Form("NarraNexus team"),
    agent_count: int = Form(1),
    thumbnail_url: Optional[str] = Form(None),
    sort_order: int = Form(0),
):
    _require_publisher(request)
    tmp = Path(tempfile.mkdtemp(prefix="nx-team-pub-"))
    try:
        # Never trust the client filename (path traversal) — fixed name.
        bundle_path = tmp / "upload.nxbundle"
        bundle_path.write_bytes(await file.read())
        cats: List[str] = [c.strip() for c in categories.split(",") if c.strip()]
        entry = await TeamMarketplaceService().publish(
            bundle_path,
            template_id=template_id,
            name=name,
            description=description,
            categories=cats,
            author=author,
            agent_count=agent_count,
            thumbnail_url=thumbnail_url,
            sort_order=sort_order,
        )
        return {"status": "published", "template_id": entry.template_id,
                "bundle_sha256": entry.bundle_sha256, "agent_count": entry.agent_count}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        logger.exception(f"Team template publish failed: {e}")
        raise HTTPException(status_code=500, detail="Publish failed")
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)


@router.delete("/templates/{template_id}")
async def delete_template(template_id: str, request: Request):
    _require_publisher(request)
    try:
        removed = await TeamMarketplaceService().delete(template_id)
    except Exception as e:
        logger.exception(f"Team template delete failed: {e}")
        raise HTTPException(status_code=500, detail="Delete failed")
    if not removed:
        raise HTTPException(status_code=404, detail={"code": "TEMPLATE_NOT_FOUND"})
    return {"status": "deleted", "template_id": template_id}
