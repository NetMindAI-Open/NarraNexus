"""
@file_name: descriptor.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.channels.narramessenger's ChannelDescriptor — the channel as one record for the ``ingress.channels`` registry.

Everything the platform used to know about NarraMessenger from six scattered
tables (trigger map, module_registry, the data-access CHANNELS table, the
WorkingSource member, the frontend row) now reads from this descriptor.
Class references are strings so registering it never imports the SDK.
"""
from __future__ import annotations

from narranexus.contracts.channel import ChannelDescriptor, ChannelUi, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registry import Contribution

_MOD = "narranexus_plugins"

DESCRIPTOR = ChannelDescriptor(
    name="narramessenger",
    display_name="NarraMessenger",
    transport="socket",
    credential_schema=CredentialSchema(
        fields=(
            CredentialField("matrix_homeserver_url", "url", label="Homeserver", required=True, public=True),
            CredentialField("matrix_user_id", "string", label="Matrix user id", required=True, public=True),
            CredentialField("matrix_access_token", "secret", label="Access token", required=True, public=False),
        ),
        supports_test=False,
        external_id_field="matrix_user_id",
        # The /sync cursor must not survive a disabled period: resumed after an off/on
        # toggle (or an import that lands disabled), Synapse would hand the subscriber
        # everything sent meanwhile and the agent would answer it. Blanked at the
        # disabled -> enabled transition => the next connect is a cursor-less first sync,
        # whose historic filter drops that backlog.
        reset_on_reenable=("matrix_since_token",),
    ),
    trigger_ref=f"{_MOD}.narramessenger_module.matrix_trigger:MatrixTrigger",
    module_ref=f"{_MOD}.narramessenger_module.narramessenger_module:NarramessengerModule",
    credential_manager_ref=f"{_MOD}.narramessenger_module._narramessenger_credential_manager:NarramessengerCredentialManager",
    credential_read_method="get",
    service_ref=f"{_MOD}.narramessenger_module._narramessenger_service",
    bind_takes="db",
    bind_fields=(
        CredentialField("bind_command", "string", label="Bind link or command", required=True, public=False),
    ),
    has_bind=True,
    has_test=False,
    unbind_service=True,  # do_unbind(db, agent_id): the gateway-side unbind rides along
    meta={
        "storage": "generic",  # 4d: the manager persists in channel_credentials
        "contact_key": "matrix",  # the key this channel's ids live under in contact_info.channels
    },
    # Message source: how this channel's replies are recognised and its stored
    # rows labelled. Descriptor fields, not a module-level
    # ``MessageSourceRegistry.register`` at import — so a distribution that drops
    # this plugin drops the handler with it, and importing the package registers
    # nothing. The extractor is a REF: naming it must not import the SDK.
    reply_tools=("narra_reply", "narra_send"),
    row_prefix_template="[NarraMessenger · {sender_name} · {sender_id} · {chat_id}]",
    reply_extractor_ref=f"{_MOD}.narramessenger_module.narramessenger_module:_extract_narramessenger_reply",
    dedicated_trigger=True,
    ui=ChannelUi(label="NarraMessenger", icon="message-circle", order=50),
)

CHANNEL = (Contribution("narramessenger", lambda: DESCRIPTOR),)

__all__ = ["CHANNEL", "DESCRIPTOR"]
