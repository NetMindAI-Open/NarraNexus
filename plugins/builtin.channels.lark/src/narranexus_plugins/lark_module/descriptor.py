"""
@file_name: descriptor.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.channels.lark's ChannelDescriptor — the channel as one record for the ``ingress.channels`` registry.

Everything the platform used to know about Lark from six scattered
tables (trigger map, module_registry, the data-access CHANNELS table, the
WorkingSource member, the frontend row) now reads from this descriptor.
Class references are strings so registering it never imports the SDK.
"""
from __future__ import annotations

from narranexus.contracts.channel import ChannelDescriptor, ChannelUi, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registry import Contribution

_MOD = "narranexus_plugins"

DESCRIPTOR = ChannelDescriptor(
    name="lark",
    display_name="Lark",
    transport="socket",
    credential_schema=CredentialSchema(
        fields=(
            CredentialField("app_id", "string", label="App ID", required=True, public=True),
            CredentialField("app_secret", "secret", label="App Secret", required=True, public=False),
            CredentialField("brand", "select", label="Brand", required=False, public=True),
        ),
        supports_test=False,
        external_id_field="app_id",
    ),
    trigger_ref=f"{_MOD}.lark_module.lark_trigger:LarkTrigger",
    module_ref=f"{_MOD}.lark_module.lark_module:LarkModule",
    credential_manager_ref=f"{_MOD}.lark_module._lark_credential_manager:LarkCredentialManager",
    credential_read_method="get_credential",
    service_ref=f"{_MOD}.lark_module._lark_service",
    bind_takes="mgr",
    bind_fields=(
        CredentialField("app_id", "string", label="App ID", required=True),
        CredentialField("app_secret", "secret", label="App Secret", required=True),
        CredentialField("brand", "select", label="Brand", required=True, options=("feishu", "lark")),
        CredentialField("owner_email", "email", label="Owner e-mail", required=False),
    ),
    has_bind=True,
    has_test=False,
    unbind_service=True,
    meta={
        "storage": "generic",  # 4d: the manager persists in channel_credentials
    },
    # Message source: how this channel's replies are recognised and its stored
    # rows labelled. Descriptor fields, not a module-level
    # ``MessageSourceRegistry.register`` at import — so a distribution that drops
    # this plugin drops the handler with it, and importing the package registers
    # nothing. The extractor is a REF: naming it must not import the SDK.
    reply_tools=("lark_cli", "notify_owner"),
    row_prefix_template="[Lark · {sender_name} in {room_name}]",
    reply_extractor_ref=f"{_MOD}.lark_module.lark_module:_extract_lark_reply",
    dedicated_trigger=True,
    ui=ChannelUi(label="Lark / Feishu", icon="message-square", order=10),
)

CHANNEL = (Contribution("lark", lambda: DESCRIPTOR),)

__all__ = ["CHANNEL", "DESCRIPTOR"]
