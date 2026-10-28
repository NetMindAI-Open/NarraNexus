"""
@file_name: test_channel_view_isolation.py
@author: Bin Liang
@date: 2026-09-07
@description: One broken channel descriptor factory must not hide every channel from SUPPORTED_CHANNELS (fail closed for THAT channel only), the view is cached on registry state (a descriptor factory runs once, not once per membership test), and a plugin module shows up in the MCP host's module list.
"""
from __future__ import annotations

from narranexus.contracts.channel import ChannelDescriptor, CredentialField, CredentialSchema
from narranexus.kernel.plugins.registries import Registries
from narranexus.kernel.plugins.registry import Contribution
from narranexus.platform.module_system.data_access.channel_store import _ChannelSpecs

GOOD = ChannelDescriptor(
    name="good_ch", display_name="Good", transport="webhook",
    credential_schema=CredentialSchema(fields=(CredentialField("api_token", "secret"),), supports_test=False),
    credential_manager_ref="narranexus.platform.channel.credential_store:GenericCredentialStore",
)


def _explode():
    raise RuntimeError("descriptor import failed")


def test_broken_descriptor_is_absent_and_the_rest_survive():
    regs = Registries()
    reg = regs.registry_for("ingress.channels")
    reg.register_contribution(Contribution("good_ch", lambda: GOOD), owner="acme.good")
    reg.register_contribution(Contribution("bad_ch", _explode), owner="acme.bad")
    view = _ChannelSpecs(regs)
    assert "good_ch" in view and "bad_ch" not in view and len(view) == 1


def test_view_is_cached_on_registry_state():
    calls: list[int] = []

    def factory():
        calls.append(1)
        return GOOD

    regs = Registries()
    reg = regs.registry_for("ingress.channels")
    reg.register_contribution(Contribution("good_ch", factory), owner="acme.good")
    view = _ChannelSpecs(regs)
    for _ in range(5):
        assert "good_ch" in view
        list(view.items())
    assert len(calls) == 1
    reg.register_contribution(Contribution("other", lambda: GOOD), owner="acme.other")
    assert "good_ch" in view and len(calls) == 2  # registry changed → rebuilt once


def test_mcp_host_lists_a_plugin_module(monkeypatch):
    from narranexus.platform.module_system import module_runner
    from narranexus.platform.module_system.base import XYZBaseModule

    class AcmeModule(XYZBaseModule):  # minimal: the runner only asks the registry for names here
        pass

    fake = {"ChatModule": object, "AcmeModule": AcmeModule}
    monkeypatch.setattr("narranexus.platform.module_system.module_registry", fake, raising=True)
    names = module_runner.all_mcp_modules()
    assert "AcmeModule" in names and "ChatModule" in names
