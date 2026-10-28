"""
@file_name: plugin_hooks.py
@author: Bin Liang
@date: 2026-09-04
@description: ``backend.hooks`` implementations the builtin.channels.narramessenger plugin ships (Manyfold credential export).

The Manyfold sync route used to import every channel's credential manager to
build its inventory; it now fires ``onWillExportManagedChannels`` and
each channel builtin answers with its own enabled bindings as uniform rows
(decoded secrets on purpose: the endpoint sits behind the gateway token and
Manyfold opens the replacement IM connections with them).
"""
from __future__ import annotations

from typing import Any

from narranexus.kernel.plugins.hooks import hookimpl


@hookimpl("onWillExportManagedChannels")
async def export_credentials(db: Any) -> list[dict[str, Any]]:
    from narranexus_plugins.narramessenger_module._narramessenger_credential_manager import NarramessengerCredentialManager

    return [
        {
            "provider": "narramessenger",
            "agent_id": cred.agent_id,
            "enabled": bool(cred.enabled),
            "external_id": cred.matrix_user_id or None,
            "connection_mode": cred.connection_mode,
            "credentials": {"matrix_access_token": cred.matrix_access_token or None},
            "config": {
                "matrix_homeserver_url": cred.matrix_homeserver_url or None,
                "matrix_user_id": cred.matrix_user_id or None,
            },
        }
        for cred in await NarramessengerCredentialManager(db).list_active()
    ]


HOOKS = (export_credentials,)

__all__ = ["HOOKS", "export_credentials"]
