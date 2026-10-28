"""
@file_name: routes.py
@author: NarraNexus
@date: 2026-08-28
@description: HTTP surface for backend.integrations.plugins.PluginService —
              lets the local/desktop Settings -> Plugins panel list, install,
              and uninstall the optional coding-agent framework plugins
              (Claude Code, Codex CLI).

Cloud deployments pre-install these platform-side (the images ship the SDKs
in the base environment, so ``framework_installed`` already reports True
without a plugin tree), so the install/uninstall verbs are local/desktop
only — a cloud caller gets 403 before anything runs. The list endpoint stays
available everywhere and returns ``cloud_managed``; the frontend uses that to
HIDE the whole Settings → Plugins nav entry + pane in cloud (SettingsPage.tsx /
PluginsSettings.tsx), because the plugin-install concept does not apply there —
its per-plugin ``installed`` (a pyenv filesystem probe) would read False in
cloud even though the frameworks are available from the base env, which would
only confuse. So the list endpoint being reachable in cloud is a backstop, not
a rendered surface.

Authentication is the ordinary global gate: this router is not in
``backend.auth.AUTH_EXEMPT_PATHS``, so ``auth_middleware`` already requires
an authenticated caller (401 otherwise) before any handler here runs. No
handler reads ``request.state.user_id`` because plugin install state is a
machine-local property, not per-user data.
"""
from __future__ import annotations

import json
from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from starlette.concurrency import run_in_threadpool

from narranexus.platform.utils.deployment_mode import is_cloud_mode

from backend.integrations.plugins.errors import PluginBusyError
from backend.integrations.plugins.service import PluginService

router = APIRouter(prefix="/api/plugins")

# Process-level singleton. PluginService keeps its "is this plugin mid-
# install" lock and busy-set as instance state (see service.py) — a fresh
# instance per request would give every request its own lock and defeat the
# re-entry guard against two concurrent package-manager processes writing
# into the same target directory.
_service = PluginService()

_CLOUD_MANAGED_DETAIL = "Plugins are managed by the platform in cloud mode"


@router.get("")
async def list_plugins():
    """List every installable plugin with its current install/version state.

    ``list_plugins`` is synchronous and forks ``claude --version`` to read the
    managed CLI's version, so it runs in a thread — a fork on the event loop
    would freeze every in-flight WebSocket/agent stream on the single-process
    desktop backend (CLAUDE.md 铁律 #16: never be the interruption source)."""
    statuses = await run_in_threadpool(_service.list_plugins)
    plugins = [asdict(status) for status in statuses]
    return {
        "success": True,
        "data": {"plugins": plugins, "cloud_managed": is_cloud_mode()},
    }


@router.post("/{plugin_id}/install")
async def install_plugin(plugin_id: str):
    """Stream newline-delimited JSON install progress for one plugin.

    An unknown ``plugin_id`` must 404, but ``PluginService.install`` is an
    async generator whose body (including the ``KeyError`` on an unknown id)
    only runs once iterated — and by the time ``StreamingResponse`` starts
    iterating, the 200 status line is already committed to the wire and can
    no longer become a 404. So the first event is pulled off the generator
    HERE, before constructing the response, and re-injected as the stream's
    first line if it looks up cleanly.
    """
    if is_cloud_mode():
        raise HTTPException(status_code=403, detail=_CLOUD_MANAGED_DETAIL)

    events = _service.install(plugin_id)
    try:
        first_event = await events.__anext__()
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown plugin id: {plugin_id!r}")
    except StopAsyncIteration:
        raise HTTPException(status_code=500, detail="Install produced no events")

    async def body():
        yield json.dumps(first_event) + "\n"
        async for event in events:
            yield json.dumps(event) + "\n"

    return StreamingResponse(body(), media_type="application/x-ndjson")


@router.post("/{plugin_id}/uninstall")
async def uninstall_plugin(plugin_id: str):
    """Remove every installed component of a plugin, local/desktop only."""
    if is_cloud_mode():
        raise HTTPException(status_code=403, detail=_CLOUD_MANAGED_DETAIL)

    try:
        await _service.uninstall(plugin_id)
    except KeyError:
        raise HTTPException(status_code=404, detail=f"Unknown plugin id: {plugin_id!r}")
    except PluginBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc))

    statuses = await run_in_threadpool(_service.list_plugins)
    new_status = next(s for s in statuses if s.id == plugin_id)
    return {"success": True, "data": asdict(new_status)}
