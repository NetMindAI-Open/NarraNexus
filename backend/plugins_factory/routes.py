"""
@file_name: routes.py
@author: Bin Liang
@date: 2026-09-03
@description: ``/api/plugin-factory`` — HTTP surface of ``FactoryService`` for the factory UI, the CLI and Nexus_Plugins_Module.

Own prefix (not ``/api/plugins``) so it never collides with the framework
installer's ``/{plugin_id}/install`` routes. Auth is the ordinary global
gate (not exempt). Cloud: reads work, mutations answer 403. Assets are
served with the traversal guard in the service and an SRI header so the
frontend loader can verify what it imports.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from narranexus.kernel.plugins.install.integrity import sri_for
from narranexus.kernel.plugins.install.installer import InstallError
from narranexus.kernel.plugins.lifecycle import RegistryError

from backend.plugins_factory.service import CloudManaged, FactoryService, NotInstalled

router = APIRouter(prefix="/api/plugin-factory")
_service: FactoryService | None = None


def service() -> FactoryService:
    global _service
    if _service is None:
        _service = FactoryService()
    return _service


def set_service(svc: FactoryService | None) -> None:
    global _service
    _service = svc


def _run(fn, *args, **kwargs):
    async def _inner():
        try:
            return await run_in_threadpool(fn, *args, **kwargs)
        except CloudManaged as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        except NotInstalled as exc:
            raise HTTPException(status_code=404, detail=str(exc))
        except (InstallError, RegistryError) as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return _inner()


class InstallBody(BaseModel):
    source: str = Field(min_length=1, max_length=512)
    permissions_acknowledged: bool = False
    scope: str = "global"


class ErrorBody(BaseModel):
    kind: str = "render"
    message: str = Field(max_length=4000)
    stack: str = Field(default="", max_length=20000)


class BisectAnswer(BaseModel):
    good: bool


@router.get("")
async def list_plugins() -> dict[str, Any]:
    return {"success": True, "data": await _run(service().list)}


@router.get("/slots")
async def slot_catalog_route() -> dict[str, Any]:
    """Every slot by domain with candidates and current binding (the process's own registries)."""
    return {"success": True, "data": await _run(service().slots)}


@router.get("/index")
async def search_index(q: str = "") -> dict[str, Any]:
    return {"success": True, "data": {"plugins": await _run(service().search_index, q)}}


@router.post("/install")
async def install(body: InstallBody) -> dict[str, Any]:
    result = await _run(service().install, body.source, permissions_acknowledged=body.permissions_acknowledged, scope=body.scope)
    return {
        "success": True,
        "data": {
            "id": result.plugin_id,
            "version": result.version,
            "path": str(result.path),
            "mode": result.mode,
            "warnings": result.warnings,
            "deps_installed": list(result.deps_installed),
            "permissions": result.permissions,
            "restart_required": result.restart_required,
        },
    }


@router.post("/builtin/{plugin_id}/enable")
async def builtin_enable(plugin_id: str) -> dict[str, Any]:
    return {"success": True, "data": await _run(service().set_builtin_enabled, plugin_id, True)}


@router.post("/builtin/{plugin_id}/install-deps")
async def builtin_install_deps(plugin_id: str) -> dict[str, Any]:
    return {"success": True, "data": await _run(service().install_builtin_deps, plugin_id)}


@router.post("/builtin/{plugin_id}/disable")
async def builtin_disable(plugin_id: str) -> dict[str, Any]:
    return {"success": True, "data": await _run(service().set_builtin_enabled, plugin_id, False)}


@router.post("/{plugin_id}/enable")
async def enable(plugin_id: str) -> dict[str, Any]:
    return {"success": True, "data": await _run(service().set_enabled, plugin_id, True)}


@router.post("/{plugin_id}/disable")
async def disable(plugin_id: str) -> dict[str, Any]:
    return {"success": True, "data": await _run(service().set_enabled, plugin_id, False)}


@router.post("/{plugin_id}/acknowledge-permissions")
async def acknowledge(plugin_id: str) -> dict[str, Any]:
    return {"success": True, "data": await _run(service().acknowledge_permissions, plugin_id)}


@router.post("/{plugin_id}/uninstall")
async def uninstall(plugin_id: str, purge: bool = True) -> dict[str, Any]:
    await _run(service().uninstall, plugin_id, purge_files=purge)
    return {"success": True, "data": {"id": plugin_id, "purged": purge}}


@router.post("/{plugin_id}/upgrade")
async def upgrade(plugin_id: str) -> dict[str, Any]:
    result = await _run(service().upgrade, plugin_id)
    return {"success": True, "data": {"id": result.plugin_id, "version": result.version, "restart_required": True}}


@router.get("/{plugin_id}/update-check")
async def update_check(plugin_id: str) -> dict[str, Any]:
    return {"success": True, "data": await _run(service().check_update, plugin_id)}


@router.post("/rollback")
async def rollback() -> dict[str, Any]:
    return {"success": True, "data": await _run(service().rollback)}


@router.post("/safe-mode/leave")
async def leave_safe_mode() -> dict[str, Any]:
    return {"success": True, "data": await _run(service().leave_safe_mode)}


@router.post("/bisect/start")
async def bisect_start() -> dict[str, Any]:
    return {"success": True, "data": await _run(service().bisect_start)}


@router.post("/bisect/answer")
async def bisect_answer(body: BisectAnswer) -> dict[str, Any]:
    return {"success": True, "data": await _run(service().bisect_answer, body.good)}


@router.post("/bisect/stop")
async def bisect_stop() -> dict[str, Any]:
    return {"success": True, "data": await _run(service().bisect_stop)}


class DecideBody(BaseModel):
    approved: bool


def _caller(request: Request) -> str:
    user_id = str(getattr(request.state, "user_id", "") or "")
    if not user_id:
        raise HTTPException(status_code=401, detail="authentication required")
    return user_id


@router.get("/proposals")
async def proposals(request: Request, pending_only: bool = True) -> dict[str, Any]:
    return {"success": True, "data": {"proposals": await _run(service().proposals, user_id=_caller(request), pending_only=pending_only)}}


@router.post("/proposals/{proposal_id}/decide")
async def decide(proposal_id: str, body: DecideBody, request: Request) -> dict[str, Any]:
    return {"success": True, "data": await _run(service().decide_proposal, proposal_id, approved=body.approved, by=_caller(request))}


@router.get("/{plugin_id}/errors")
async def errors(plugin_id: str) -> dict[str, Any]:
    return {"success": True, "data": {"errors": service().errors(plugin_id)}}


@router.post("/{plugin_id}/errors")
async def report_error(plugin_id: str, body: ErrorBody) -> dict[str, Any]:
    # A mutation like every other: cloud-guarded inside the service method (so
    # `_run` answers 403, not 500), off the event loop (the audit write
    # fsyncs), 404 for a plugin that is not installed.
    count = await _run(service().record_error, plugin_id, kind=body.kind, message=body.message, stack=body.stack)
    return {"success": True, "data": {"count": count}}


@router.get("/{plugin_id}/assets/{path:path}")
async def asset(plugin_id: str, path: str, request: Request):
    try:
        file_path = await run_in_threadpool(service().asset_path, plugin_id, path)
    except NotInstalled:
        raise HTTPException(status_code=404, detail="plugin not installed")
    except PermissionError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="asset not found")
    headers = {"X-Content-Integrity": sri_for(file_path), "Cache-Control": "no-cache"}
    return FileResponse(file_path, headers=headers)


__all__ = ["router", "service", "set_service"]
