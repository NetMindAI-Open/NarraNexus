"""
@file_name: plugin_hooks.py
@author: Bin Liang
@date: 2026-09-04
@description: ``backend.hooks`` implementations the builtin.channels.lark plugin ships (Manyfold credential export).

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
    from narranexus_plugins.lark_module._lark_credential_manager import LarkCredentialManager

    return [
        {
            "provider": "lark",
            "agent_id": cred.agent_id,
            # get_active_credentials already filters is_active=1, so
            # receive_enabled() (has a decodable secret) is the whole
            # remaining question — one home with the trigger's own gate.
            "enabled": cred.receive_enabled(),
            "external_id": cred.app_id or None,
            "credentials": {"app_secret": cred.get_app_secret()},
            "config": {"app_id": cred.app_id, "brand": cred.brand or "feishu"},
        }
        for cred in await LarkCredentialManager(db).get_active_credentials()
    ]


HOOKS = (export_credentials,)

__all__ = ["HOOKS", "export_credentials"]
