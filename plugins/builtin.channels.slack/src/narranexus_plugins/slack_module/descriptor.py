"""
@file_name: descriptor.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.channels.slack's ChannelDescriptor — the channel as one record for the ``ingress.channels`` registry.

Everything the platform used to know about Slack from six scattered
tables (trigger map, module_registry, the data-access CHANNELS table, the
WorkingSource member, the frontend row) now reads from this descriptor.
Class references are strings so registering it never imports the SDK.
"""
from __future__ import annotations

from narranexus.contracts.channel import ChannelDescriptor, ChannelUi, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registry import Contribution

_MOD = "narranexus_plugins"

DESCRIPTOR = ChannelDescriptor(
    name="slack",
    display_name="Slack",
    transport="socket",
    credential_schema=CredentialSchema(
        fields=(
            CredentialField("bot_token", "secret", label="Bot token", required=True, public=False),
            CredentialField("app_token", "secret", label="App-level token", required=True, public=False),
            CredentialField("team_id", "string", label="Workspace", required=False, public=True),
            CredentialField("bot_user_id", "string", label="Bot user id", required=False, public=True),
        ),
        supports_test=True,
        external_id_field="bot_user_id",
    ),
    trigger_ref=f"{_MOD}.slack_module.slack_trigger:SlackTrigger",
    module_ref=f"{_MOD}.slack_module.slack_module:SlackModule",
    credential_manager_ref=f"{_MOD}.slack_module._slack_credential_manager:SlackCredentialManager",
    credential_read_method="get",
    service_ref=f"{_MOD}.slack_module._slack_service",
    bind_takes="mgr",
    bind_fields=(
        CredentialField("bot_token", "secret", label="Bot token", required=True),
        CredentialField("app_token", "secret", label="App-level token", required=True),
        CredentialField("owner_email", "string", label="Owner e-mail", required=False),
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
    reply_tools=("slack_cli", "notify_owner"),
    row_prefix_template="[Slack · {sender_name} · {sender_id} · {chat_id}]",
    reply_extractor_ref=f"{_MOD}.slack_module.slack_module:_extract_slack_reply",
    dedicated_trigger=True,
    ui=ChannelUi(label="Slack", icon="hash", order=20),
)

CHANNEL = (Contribution("slack", lambda: DESCRIPTOR),)

__all__ = ["CHANNEL", "DESCRIPTOR"]
