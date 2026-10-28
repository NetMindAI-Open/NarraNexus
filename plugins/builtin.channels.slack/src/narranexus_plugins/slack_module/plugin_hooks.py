"""
@file_name: plugin_hooks.py
@author: Bin Liang
@date: 2026-09-04
@description: ``backend.hooks`` implementations the builtin.channels.slack plugin ships (Manyfold credential export).

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
    from narranexus_plugins.slack_module._slack_credential_manager import SlackCredentialManager

    return [
        # Socket Mode credentials — Manyfold cannot consume them (its Slack
        # provider is Events-API + signing secret) and skips slack rows; still
        # reported so the inventory stays faithful.
        {
            "provider": "slack",
            "agent_id": cred.agent_id,
            "enabled": bool(cred.enabled),
            "external_id": cred.bot_user_id or None,
            "credentials": {"bot_token": cred.bot_token, "app_token": cred.app_token},
            "config": {"team_id": cred.team_id or None},
        }
        for cred in await SlackCredentialManager(db).list_active()
    ]


HOOKS = (export_credentials,)

__all__ = ["HOOKS", "export_credentials"]
