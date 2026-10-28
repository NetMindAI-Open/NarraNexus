"""
@file_name: descriptor.py
@author: Bin Liang
@date: 2026-09-04
@description: builtin.home_assistant's ChannelDescriptor — credentials only (no inbound transport), so the data-access channel seam knows its credential manager.
"""
from __future__ import annotations

from narranexus.contracts.channel import ChannelDescriptor, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registry import Contribution

DESCRIPTOR = ChannelDescriptor(
    name="home_assistant",
    display_name="Home Assistant",
    transport="none",
    credential_schema=CredentialSchema(
        fields=(
            CredentialField("base_url", "url", label="Home Assistant URL", required=True, public=True),
            CredentialField("token", "secret", label="Long-lived access token", required=True, public=False),
        ),
        supports_test=False,
    ),
    credential_manager_ref="narranexus_plugins.home_assistant_module._home_assistant_impl.binding:HomeAssistantCredentialManager",
    module_ref="narranexus_plugins.home_assistant_module.home_assistant_module:HomeAssistantModule",
    has_bind=False,
    has_test=False,
)

CHANNEL = (Contribution("home_assistant", lambda: DESCRIPTOR),)

__all__ = ["CHANNEL", "DESCRIPTOR"]
