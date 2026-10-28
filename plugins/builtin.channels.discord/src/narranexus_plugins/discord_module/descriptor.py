"""
@file_name: descriptor.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.channels.discord's ChannelDescriptor — the channel as one record for the ``ingress.channels`` registry.

Everything the platform used to know about Discord from six scattered
tables (trigger map, module_registry, the data-access CHANNELS table, the
WorkingSource member, the frontend row) now reads from this descriptor.
Class references are strings so registering it never imports the SDK.
"""
from __future__ import annotations

from narranexus.contracts.channel import ChannelDescriptor, ChannelUi, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registry import Contribution

_MOD = "narranexus_plugins"

DESCRIPTOR = ChannelDescriptor(
    name="discord",
    display_name="Discord",
    transport="socket",
    credential_schema=CredentialSchema(
        fields=(
            CredentialField("bot_token", "secret", label="Bot token", required=True, public=False),
            CredentialField("bot_username", "string", label="Bot username", required=False, public=True),
            CredentialField("bot_user_id", "string", label="Bot user id", required=False, public=True),
        ),
        supports_test=True,
        external_id_field="bot_user_id",
    ),
    trigger_ref=f"{_MOD}.discord_module.discord_trigger:DiscordTrigger",
    module_ref=f"{_MOD}.discord_module.discord_module:DiscordModule",
    credential_manager_ref=f"{_MOD}.discord_module._discord_credential_manager:DiscordCredentialManager",
    credential_read_method="get",
    service_ref=f"{_MOD}.discord_module._discord_service",
    bind_takes="mgr",
    bind_fields=(
        CredentialField("bot_token", "secret", label="Bot token", required=True),
        CredentialField("owner_user_id", "string", label="Owner user id", required=False),
    ),
    has_bind=True,
    has_test=True,
    unbind_service=False,
    meta={"storage": "generic"},  # 4d: the manager persists in channel_credentials; no mirror needed
    # Message source: how this channel's replies are recognised and its stored
    # rows labelled. Descriptor fields, not a module-level
    # ``MessageSourceRegistry.register`` at import — so a distribution that drops
    # this plugin drops the handler with it, and importing the package registers
    # nothing. The extractor is a REF: naming it must not import the SDK.
    reply_tools=("discord_send", "discord_reply", "discord_dm", "notify_owner"),
    row_prefix_template="[Discord · {sender_name} · {sender_id} · {chat_id}]",
    reply_extractor_ref=f"{_MOD}.discord_module.discord_module:_extract_discord_reply",
    dedicated_trigger=True,
    ui=ChannelUi(label="Discord", icon="bot", order=60),
)

CHANNEL = (Contribution("discord", lambda: DESCRIPTOR),)

__all__ = ["CHANNEL", "DESCRIPTOR"]
