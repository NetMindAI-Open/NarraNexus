"""
@file_name: plugin_hooks.py
@author: Bin Liang
@date: 2026-09-04
@description: ``backend.hooks`` implementations the builtin.channels.wechat plugin ships (Manyfold credential export).

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
    from narranexus_plugins.wechat_module._wechat_credential_manager import WeChatCredentialManager

    return [
        {
            "provider": "wechat",
            "agent_id": cred.agent_id,
            "enabled": bool(cred.enabled),
            "external_id": cred.bot_wx_id or None,
            "credentials": {"bot_token": cred.bot_token, "base_url": cred.base_url or None},
            "config": {"bot_wx_id": cred.bot_wx_id or None},
        }
        for cred in await WeChatCredentialManager(db).list_active()
    ]


HOOKS = (export_credentials,)

__all__ = ["HOOKS", "export_credentials"]
