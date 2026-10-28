"""
@file_name: test_channel_tables_are_registry_driven.py
@author: Bin Liang
@date: 2026-09-04
@description: Batch 4 exit: the platform holds no channel-name table — agent-level channel instances, dashboard kinds, contact keys and the manyfold payload order all come from the ingress.channels registry, so a plugin channel is treated like a builtin.
"""
from __future__ import annotations

import pytest

from narranexus.contracts.channel import ChannelDescriptor, ChannelUi, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registries import KERNEL_REGISTRIES
from narranexus.kernel.plugins.registry import Contribution

PLUGIN = ChannelDescriptor(
    name="acme_chat",
    display_name="Acme Chat",
    transport="webhook",
    credential_schema=CredentialSchema(fields=(CredentialField("bot_token", "secret"),), supports_test=False),
    module_ref="tests.plugins.hello_channel.backend:HelloChannelModule",
    has_test=False,
    ui=ChannelUi(label="Acme Chat", order=5),
    meta={"contact_key": "acme_handle"},
)


@pytest.fixture
def plugin_channel():
    import narranexus.platform.module_system  # noqa: F401

    from narranexus.platform.schema.hook_schema import WorkingSource

    WorkingSource.register("acme_chat")
    dispose = KERNEL_REGISTRIES.registry_for("ingress.channels").register_contribution(Contribution("acme_chat", lambda: PLUGIN), owner="acme.chat")
    # the plugin's module declares its agent-level instance (batch 5b)
    from tests.plugins.hello_channel.backend import HelloChannelModule
    from narranexus.platform.module_system.slots import MODULES_SLOT
    from narranexus.platform.schema.module_schema import ModuleAgentInstance, ModuleConfig

    class AcmeChatModule(HelloChannelModule):
        @staticmethod
        def get_config() -> ModuleConfig:
            return ModuleConfig(name="AcmeChatModule", priority=9, enabled=True, description="Acme chat", agent_instance=ModuleAgentInstance(description="Acme chat bot", keywords=["acme"], topic_hint="Acme"))

    dispose_mod = KERNEL_REGISTRIES.registry_for(MODULES_SLOT).register_contribution(Contribution("AcmeChatModule", lambda: AcmeChatModule, meta={"plugin_id": "acme.chat", "channel": True}), owner="acme.chat")
    yield PLUGIN
    dispose_mod.dispose()
    dispose.dispose()


@pytest.mark.asyncio
async def test_agent_level_channel_instances_come_from_descriptor_meta(db_client, plugin_channel):
    from narranexus.platform.module_system._module_impl.instance_factory import InstanceFactory

    factory = InstanceFactory(db_client)
    instances = await factory.create_agent_level_instances("agent_x")
    by_class = {i.module_class: i for i in instances}
    # builtins declare theirs (Lark, Home Assistant) and the plugin's rides along
    assert by_class["LarkModule"].instance_id.startswith("lark_") and "feishu" in by_class["LarkModule"].keywords
    assert by_class["HomeAssistantModule"].instance_id.startswith("homeassistant_")
    acme = by_class["AcmeChatModule"]
    assert acme.instance_id.startswith("acmechat_") and acme.description == "Acme chat bot" and acme.is_public
    # the core four come from their declarations too
    assert {"AwarenessModule", "SocialNetworkModule", "BasicInfoModule", "MessageBusModule"} <= set(by_class)
    assert by_class["BasicInfoModule"].instance_id.startswith("info_")
    # a channel without the meta (slack) gets none; ensure_* is idempotent
    assert "SlackModule" not in by_class
    again = await factory.ensure_agent_instances_exist("agent_x")
    assert sorted(i.module_class for i in again) == sorted(by_class)


def test_dashboard_kind_and_session_kind_follow_the_registry(plugin_channel):
    from backend.routes.dashboard._helpers import classify_kind
    from backend.routes.dashboard.routes import _derive_kind

    assert classify_kind("lark") == "LARK" and classify_kind("acme_chat") == "ACME_CHAT"
    assert classify_kind("chat") == "CHAT" and classify_kind("nope") == "idle" and classify_kind(None) == "idle"

    class _S:
        def __init__(self, channel):
            self.channel = channel

    assert _derive_kind([_S("acme_chat")], [], []) == "MESSAGE_BUS"
    assert _derive_kind([_S("lark_abc")], [], []) == "MESSAGE_BUS"
    assert _derive_kind([_S("web")], [], []) == "CHAT"


def test_contact_keys_and_manyfold_order_follow_the_registry(plugin_channel):
    from backend.routes.manyfold.sync import _provider_rank
    from narranexus.platform.channel.channel_contact_utils import contact_channel_keys, normalize_contact_info

    keys = contact_channel_keys()
    assert {"slack", "telegram", "discord", "matrix", "acme_chat", "acme_handle"} <= keys
    norm = normalize_contact_info({"acme_chat": "acme-123", "email": "a@x"})
    assert norm == {"email": "a@x", "channels": {"acme_chat": {"id": "acme-123"}}}
    # ui.order drives the payload order; unknown providers sort last
    assert _provider_rank("acme_chat") < _provider_rank("lark") < _provider_rank("zzz_unknown")
