from pathlib import Path

from narranexus.sdk.testing import PluginTestHost

PLUGIN_DIR = Path(__file__).resolve().parents[1]


def test_descriptor_trigger_and_module_register(tmp_path):
    with PluginTestHost(PLUGIN_DIR, tmp_path / "home", role="workers") as host:
        (channel,) = host.names("ingress.channels")
        assert host.names("ingress.triggers") == (channel,)
        (module_name,) = host.names("agent.capabilities.modules")
        descriptor = host.registry("ingress.channels").get(channel)
        assert descriptor.has_inbound and descriptor.transport == "webhook"
        trigger_cls = host.registry("ingress.triggers").get(channel).resolve()
        assert trigger_cls.channel_name == channel
        assert host.registry("agent.capabilities.modules").get(module_name).channel_name == channel
        raw = {"message_id": "m1", "chat_id": "c1", "sender_id": "u1", "text": "hi"}
        assert trigger_cls().parse_event(raw).content == "hi"
        assert trigger_cls().parse_event({"message_id": "m2"}) is None  # empty text is dropped
