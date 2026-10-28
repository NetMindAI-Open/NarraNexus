"""
@file_name: descriptor.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.channels.wechat's ChannelDescriptor — the channel as one record for the ``ingress.channels`` registry.

Everything the platform used to know about WeChat from six scattered
tables (trigger map, module_registry, the data-access CHANNELS table, the
WorkingSource member, the frontend row) now reads from this descriptor.
Class references are strings so registering it never imports the SDK.
"""
from __future__ import annotations

from narranexus.contracts.channel import ChannelDescriptor, ChannelUi, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registry import Contribution

_MOD = "narranexus_plugins"

DESCRIPTOR = ChannelDescriptor(
    name="wechat",
    display_name="WeChat",
    transport="socket",
    credential_schema=CredentialSchema(
        fields=(
            CredentialField("bot_token", "secret", label="Bot token", required=True, public=False),
            CredentialField("base_url", "url", label="Base URL", required=False, public=True),
            CredentialField("bot_wx_id", "string", label="Bot WeChat id", required=False, public=True),
        ),
        supports_test=False,
        external_id_field="bot_wx_id",
    ),
    trigger_ref=f"{_MOD}.wechat_module.wechat_trigger:WeChatTrigger",
    module_ref=f"{_MOD}.wechat_module.wechat_module:WeChatModule",
    credential_manager_ref=f"{_MOD}.wechat_module._wechat_credential_manager:WeChatCredentialManager",
    credential_read_method="get",
    service_ref="",
    bind_takes="mgr",
    has_bind=False,
    has_test=False,
    unbind_service=False,
    meta={"storage": "generic"},  # 4d: the manager persists in channel_credentials; no mirror needed
    # Message source: how this channel's replies are recognised and its stored
    # rows labelled. Descriptor fields, not a module-level
    # ``MessageSourceRegistry.register`` at import — so a distribution that drops
    # this plugin drops the handler with it, and importing the package registers
    # nothing. The extractor is a REF: naming it must not import the SDK.
    reply_tools=("wechat_send", "notify_owner"),
    row_prefix_template="[WeChat · {sender_name} · {sender_id}]",
    reply_extractor_ref=f"{_MOD}.wechat_module.wechat_module:_extract_wechat_reply",
    dedicated_trigger=True,
    ui=ChannelUi(label="WeChat", icon="qr-code", order=40),
)

CHANNEL = (Contribution("wechat", lambda: DESCRIPTOR),)

__all__ = ["CHANNEL", "DESCRIPTOR"]
